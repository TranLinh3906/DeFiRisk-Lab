-- N3 | Aave V3 Ethereum LiquidationCall, [2026-09-15, 2026-10-16) UTC
-- Output schema used by fetch_liquidations.py. Run in Dune SQL editor.
-- Price/decimals may be NULL: Python will retain them as missing, never fake USD=0.
WITH liq AS (
    SELECT
        l.evt_block_time AS event_timestamp,
        l.evt_block_number AS event_block,
        l.evt_index AS log_index,
        CAST(l.evt_tx_hash AS VARCHAR) AS tx_hash,
        CAST(l."user" AS VARCHAR) AS wallet_address,
        CAST(l.liquidator AS VARCHAR) AS liquidator_address,
        CAST(l.collateralAsset AS VARCHAR) AS collateral_token_address,
        CAST(l.debtAsset AS VARCHAR) AS debt_token_address,
        l.collateralAsset AS collateral_token_binary,
        l.debtAsset AS debt_token_binary,
        CAST(l.debtToCover AS VARCHAR) AS debt_raw_amount,
        CAST(l.liquidatedCollateralAmount AS VARCHAR) AS collateral_raw_amount
    FROM aave_v3_ethereum.Pool_evt_LiquidationCall l
    WHERE l.evt_block_time >= TIMESTAMP '2026-09-15 00:00:00'
      AND l.evt_block_time <  TIMESTAMP '2026-10-16 00:00:00'
),
meta AS (
    SELECT blockchain, contract_address, symbol, decimals
    FROM tokens.erc20
    WHERE blockchain = 'ethereum'
),
pr AS (
    SELECT minute, contract_address, price
    FROM prices.usd
    WHERE blockchain = 'ethereum'
      AND minute >= TIMESTAMP '2026-09-15 00:00:00'
      AND minute < TIMESTAMP '2026-10-16 00:00:00'
)
SELECT
    l.tx_hash, l.log_index, l.event_block, l.event_timestamp,
    'aave_v3_ethereum' AS protocol_id,
    l.wallet_address, l.liquidator_address,
    l.collateral_token_address, l.debt_token_address,
    l.debt_raw_amount, l.collateral_raw_amount,
    CAST(md.decimals AS INTEGER) AS debt_decimals,
    CAST(mc.decimals AS INTEGER) AS collateral_decimals,
    md.symbol AS debt_symbol, mc.symbol AS collateral_symbol,
    pd.price AS debt_price_usd,
    pc.price AS collateral_price_usd
FROM liq l
LEFT JOIN meta md ON md.contract_address = l.debt_token_binary
LEFT JOIN meta mc ON mc.contract_address = l.collateral_token_binary
LEFT JOIN pr pd ON pd.contract_address = l.debt_token_binary
               AND pd.minute = date_trunc('minute', l.event_timestamp)
LEFT JOIN pr pc ON pc.contract_address = l.collateral_token_binary
               AND pc.minute = date_trunc('minute', l.event_timestamp)
ORDER BY l.event_timestamp, l.tx_hash, l.log_index
