"""Conditional Comet absorb and collateral resale, kept separate from Aave.

Follows Compound III's two-step accounting in its native base token. This is
floating-point research bookkeeping, not contract integer/interest emulation.
Reference: https://docs.compound.finance/liquidation/
"""
import math
import pandas as pd


def simulate_comet(exposures, config, market_state, returns, routes, *, gas_usd=5.0, initial_pool_volumes=None):
    """Absorb eligible overlap accounts, then offer whole inventory for resale.

    Inventory trade size and liquidator ordering are explicit counterfactuals.
    Unquoted collateral is retained as inventory, never silently sold.
    """
    if market_state is None:
        return [], dict(status='missing_observed_reserves_pause_and_storefront_state')
    initial_flows=dict(initial_pool_volumes or {})
    if any(not math.isfinite(v) or v<0 for v in initial_flows.values()):
        raise ValueError('Invalid initial cumulative pool volume')
    block = int(market_state['snapshot_block'])
    market = market_state['market_id']
    p = exposures[exposures.snapshot_block.eq(block) & exposures.market_id.eq(market)].copy()
    cfg = config[config.snapshot_block.eq(block) & config.market_id.eq(market)].set_index('token_address')
    if cfg.index.duplicated().any():
        raise ValueError('Duplicate Comet configuration')
    base = market_state['baseToken'].lower()
    p['change'] = [float(returns.get((block, r.token_address), 0)) for r in p.itertuples()]
    if not p.change.map(lambda v: math.isfinite(v) and v > -1).all():
        raise ValueError('Invalid Comet shock')
    p['stressed_value'] = p.value_usd * (1 + p.change)
    if p.empty:
        return [], dict(status='no_overlap_positions')
    base_rows = p[p.token_address.str.lower().eq(base)]
    if base_rows.empty:
        raise ValueError('Missing Comet base conversion')
    base_price = float(base_rows.price_usd.iloc[0]) * (1 + float(returns.get((block, base), 0)))
    rate = float(base_rows.usd_conversion_rate.iloc[0]) if 'usd_conversion_rate' in base_rows else 1.0
    scale = int(market_state['baseScale'])
    reserves = int(market_state['getReserves']) / scale
    initial_reserves = reserves
    target = int(market_state['targetReserves']) / scale
    storefront = int(market_state['storeFrontPriceFactor']) / 1e18
    if not 0 <= storefront <= 1:
        raise ValueError('Invalid Comet storefront factor')
    inventory = {}
    for token, raw in market_state['collateral_reserves_raw'].items():
        inventory[token] = int(raw) / float(cfg.loc[token, 'scale'])
    events = []
    for address, account in p.groupby('wallet_address', sort=True):
        collateral = account[account.asset_role.eq('collateral')]
        debt = float(account.loc[account.asset_role.eq('base_borrow'), 'stressed_value'].sum())
        capacity = float((collateral.stressed_value * collateral.liquidate_collateral_factor).sum())
        if debt <= capacity:
            continue
        if market_state['isAbsorbPaused']:
            events.append(dict(action='absorb_blocked_paused', wallet_address=address))
            continue
        penalized_value = 0.0
        for r in collateral.itertuples():
            factor = float(cfg.loc[r.token_address, 'liquidation_factor'])
            if not math.isfinite(factor) or not 0 <= factor <= 1:
                raise ValueError('Missing Comet liquidation penalty factor')
            penalized_value += float(r.stressed_value) * factor
            inventory[r.token_address] = inventory.get(r.token_address, 0) + float(r.amount_token)
        credit_usd = max(0.0, penalized_value - debt)
        reserve_delta = -(debt + credit_usd) / base_price
        reserves += reserve_delta
        events.append(dict(action='absorb', wallet_address=address, debt_cleared_usd=debt,
            borrower_base_supply_credit_usd=credit_usd, reserves_delta_base=reserve_delta,
            seized_collateral_usd=float(collateral.stressed_value.sum())))
    sold, unknown = initial_flows.copy(), 0
    for token, qty in sorted(inventory.items()):
        if qty <= 0 or reserves >= target or market_state['isBuyPaused']:
            continue
        row = cfg.loc[token]
        native = float(row.price_usd)
        snapshot_usd = native * rate
        price = snapshot_usd * (1 + float(returns.get((block, token), 0)))
        discount = storefront * (1 - float(row.liquidation_factor))
        route = routes.get((token, base))
        if route is None:
            unknown += 1
            events.append(dict(action='buy_unknown_route', token_address=token, inventory_tokens=qty))
            continue
        pool = route['pool_address']
        before = sold.get(pool, 0.0)
        notional = qty * snapshot_usd
        try:
            slip = route['curve'].incremental(before, notional)
        except ValueError:
            unknown += 1
            events.append(dict(action='buy_unknown_quote_domain', token_address=token, inventory_tokens=qty))
            continue
        payment_usd = qty * price * (1 - discount)
        profit = qty * price * (1 - slip) * (1 - route.get('fee_rate', 0)) - payment_usd - gas_usd
        if profit <= 0:
            events.append(dict(action='buy_unprofitable_under_assumptions', token_address=token, inventory_tokens=qty))
            continue
        payment_base = payment_usd / base_price
        reserves += payment_base
        sold[pool] = before + notional
        inventory[token] = 0.0
        events.append(dict(action='buy_collateral', token_address=token, sold_tokens=qty,
            base_paid=payment_base, base_paid_usd=payment_usd, liquidator_profit_usd=profit,
            discount_fraction=discount, slippage_fraction=slip))
    return events, dict(status='conditional_absorb_and_inventory_resale',
        initial_reserves_base=initial_reserves, final_reserves_base=reserves,
        target_reserves_base=target, absorbed_accounts=sum(e['action'] == 'absorb' for e in events),
        initial_pool_volumes_snapshot_usd=initial_flows,final_pool_volumes_snapshot_usd=sold,
        collateral_purchases=sum(e['action'] == 'buy_collateral' for e in events),
        unknown_inventory_routes=unknown, residual_inventory_tokens=inventory,
        interpretation='overlap_accounts_only_whole_inventory_offers_not_full_contract_emulation')
