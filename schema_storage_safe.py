import os
import json
import shutil
import tempfile
from datetime import datetime, timezone

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# PHIEN BAN LUU TRU AN TOAN
# - Khong thay doi 6 bang, ten cot, kieu du lieu, PK/FK contract hoac duong dan.
# - init_empty_tables_with_keys() mac dinh bo qua file da ton tai va kiem tra schema.
# - save_table_with_key_check() mac dinh append an toan; co che do upsert va replace_partition tuong minh.
# - Chi mot tien trinh duoc ghi vao cung mot file Parquet tai mot thoi diem.
# Doc huong dan truoc khi chay trong moi truong du lieu that.


# ==============================================================================
# 1. ĐỊNH NGHĨA QUY TẮC KHÓA CHÍNH (PK) & KHÓA NGOẠI (FK) CHO CẢ 6 BẢNG
# ==============================================================================
TABLE_KEY_CONTRACTS = {
    "dim_asset_config": {
        "path": "data/processed/dim_asset_config.parquet",
        "primary_keys": ["snapshot_block", "protocol_id", "token_address"],
        "foreign_keys": [],
        "address_cols": ["token_address"],
    },
    "fact_wallet_positions": {
        "path": "data/processed/fact_wallet_positions.parquet",
        "primary_keys": ["snapshot_block", "protocol_id", "wallet_address", "token_address"],
        "foreign_keys": [
            {
                "parent_table": "dim_asset_config",
                "parent_path": "data/processed/dim_asset_config.parquet",
                "join_columns": ["snapshot_block", "protocol_id", "token_address"],
            }
        ],
        "address_cols": ["wallet_address", "token_address"],
    },
    "fact_wallet_summary": {
        "path": "data/processed/fact_wallet_summary.parquet",
        "primary_keys": ["snapshot_block", "protocol_id", "wallet_address"],
        "foreign_keys": [
            {
                "parent_table": "fact_wallet_positions",
                "parent_path": "data/processed/fact_wallet_positions.parquet",
                "join_columns": ["snapshot_block", "protocol_id", "wallet_address"],
            }
        ],
        "address_cols": ["wallet_address"],
    },
    "graph_network_edges": {
        "path": "data/processed/graph_network_edges.parquet",
        "primary_keys": ["snapshot_block", "edge_id"],
        "foreign_keys": [],
        "address_cols": [],
    },
    "out_simulation_results": {
        "path": "outputs/tables/out_simulation_results.parquet",
        "primary_keys": [
            "run_id",
            "question_id",
            "scenario_name",
            "cf_rule",
            "cascade_round",
            "protocol_id",
            "segment_group",
        ],
        "foreign_keys": [],
        "address_cols": [],
    },
    "fact_historical_liquidations": {
        "path": "data/processed/fact_historical_liquidations.parquet",
        "primary_keys": ["tx_hash", "log_index"],
        "foreign_keys": [
            {
                "parent_table": "dim_asset_config",
                "parent_path": "data/processed/dim_asset_config.parquet",
                "join_columns": ["protocol_id", "collateral_token_address->token_address"],
            }
        ],
        "address_cols": [
            "wallet_address",
            "liquidator_address",
            "collateral_token_address",
            "debt_token_address",
        ],
    },
}


# Khi can chay lai mot snapshot/run da ton tai, replace_partition chi thay the dung pham vi nay.
# Khong them fact_historical_liquidations vao day: thay the theo event_block chi an toan neu batch
# dau vao bao phu day du tat ca liquidation events cua moi block duoc thay the.
REPLACE_PARTITION_COLUMNS = {
    "dim_asset_config": ["snapshot_block", "protocol_id"],
    "fact_wallet_positions": ["snapshot_block", "protocol_id"],
    "fact_wallet_summary": ["snapshot_block", "protocol_id"],
    "graph_network_edges": ["snapshot_block", "protocol_id"],
    "out_simulation_results": ["run_id"],
}


def _check_schema_compatible(existing_schema: pa.Schema, expected_schema: pa.Schema, table_name: str) -> None:
    """Raise loi neu schema file hien tai khong khop voi schema duoc dinh nghia trong code."""
    if existing_schema.names != expected_schema.names:
        raise ValueError(
            f"Schema khong khop cho '{table_name}'.\n"
            f"Cot trong file: {existing_schema.names}\n"
            f"Cot theo code: {expected_schema.names}.\n"
            "Khong tu dong ghi de; hay kiem tra migration/schema version truoc."
        )

    mismatches = []
    for expected_field in expected_schema:
        existing_field = existing_schema.field(expected_field.name)
        if existing_field.type != expected_field.type or existing_field.nullable != expected_field.nullable:
            mismatches.append(
                f"{expected_field.name}: file=({existing_field.type}, nullable={existing_field.nullable}), "
                f"code=({expected_field.type}, nullable={expected_field.nullable})"
            )
    if mismatches:
        raise ValueError(
            f"Schema khong khop cho '{table_name}':\n - " + "\n - ".join(mismatches)
            + "\nKhong tu dong ghi de file du lieu."
        )

    # Kiem tra metadata PK/FK de tranh dung nham file Parquet cua bang khac.
    existing_meta = existing_schema.metadata or {}
    expected_meta = expected_schema.metadata or {}
    for key in (b"table_name", b"primary_keys", b"foreign_keys", b"address_columns"):
        if key in expected_meta and existing_meta.get(key) != expected_meta.get(key):
            raise ValueError(
                f"Metadata schema khong khop cho '{table_name}', key={key.decode('utf-8')}. "
                "Khong tu dong ghi de file."
            )


def _write_parquet_atomically(table: pa.Table, target_path: str) -> None:
    """Ghi file tam cung thu muc roi thay the atomically de giam nguy co file dang ghi bi hong."""
    target_dir = os.path.dirname(target_path) or "."
    os.makedirs(target_dir, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=os.path.basename(target_path) + ".",
        suffix=".tmp",
        dir=target_dir,
    )
    os.close(fd)
    try:
        pq.write_table(table, temp_path, compression="snappy")
        os.replace(temp_path, target_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def _rows_as_tuples(df: pd.DataFrame, columns: list[str]) -> list[tuple]:
    """Chuyen cac cot khoa thanh tuple de so sanh khoa mot cach nhat quan."""
    return list(df[columns].itertuples(index=False, name=None))


def _attach_key_metadata(schema: pa.Schema, table_name: str) -> pa.Schema:
    """Nhúng thông tin Primary Key và Foreign Key vào thẳng Metadata của file Parquet."""
    contract = TABLE_KEY_CONTRACTS[table_name]
    meta = {
        b"table_name": table_name.encode("utf-8"),
        b"primary_keys": json.dumps(contract["primary_keys"]).encode("utf-8"),
        b"foreign_keys": json.dumps(contract["foreign_keys"]).encode("utf-8"),
        b"address_columns": json.dumps(contract["address_cols"]).encode("utf-8"),
    }
    return schema.with_metadata(meta)


# ==============================================================================
# 2. KHỞI TẠO 6 BẢNG TRỐNG VỚI RÀNG BUỘC NULLABLE=FALSE Ở CÁC CỘT KHÓA
# ==============================================================================
def init_empty_tables_with_keys(overwrite: bool = False):
    """Tao cac bang neu chua co; mac dinh KHONG ghi de file dang ton tai.

    overwrite=True chi dung khi chu dong muon tao lai bang rong. Neu file ton tai,
    ban sao du phong .bak_<UTC timestamp> duoc tao truoc khi thay the.
    """
    os.makedirs("data/processed", exist_ok=True)
    os.makedirs("outputs/tables", exist_ok=True)

    # Bảng 1: dim_asset_config (PK: snapshot_block, protocol_id, token_address)
    s1 = _attach_key_metadata(
        pa.schema([
            pa.field("snapshot_block", pa.int64(), nullable=False),  # PK | Số block của snapshot/record; cần khớp với block được dùng để nối các bảng liên quan.
            pa.field("snapshot_timestamp", pa.timestamp("ns", tz="UTC"), nullable=False),  # Thời điểm UTC tương ứng với snapshot_block. | Nên lấy từ timestamp của block, không lấy thời điểm máy chạy script.
            pa.field("protocol_id", pa.string(), nullable=False),  # PK | Mã protocol/market theo danh mục định danh chung của nhóm; phải nhất quán giữa các bảng.
            pa.field("token_address", pa.string(), nullable=False),  # PK | Địa chỉ contract token; dùng làm khóa định danh tài sản, chuẩn hóa chữ thường.
            pa.field("token_symbol", pa.string(), nullable=False),  # Ký hiệu dễ đọc của token, ví dụ WETH, USDC, wstETH. | Chỉ để hiển thị; không đảm bảo duy nhất và có thể trùng giữa chain/token.
            pa.field("asset_class", pa.string(), nullable=False),  # Nhóm loại tài sản để phân tích, ví dụ stablecoin, wrapped_native, liquid_staking_token. | Nhóm cần có danh mục enum chung; không để mỗi thành viên tự đặ...
            pa.field("decimals", pa.int32(), nullable=False),  # Số chữ số thập phân của token trên chain. | Dùng để chuẩn hóa amount_raw thành đơn vị token: amount_norm = amount_raw / 10**decimals.
            pa.field("price_usd_t0", pa.float64(), nullable=False),  # Giá USD của một token tại thời điểm snapshot. | Đơn vị USD/token. Chỉ rõ nguồn giá (oracle/DEX) và quy tắc timestamp trong metadata hoặc nhật ký pipeline.
            pa.field("price_eth_t0", pa.float64(), nullable=False),  # Giá một token quy đổi theo ETH tại snapshot. | Đơn vị ETH/token; cần quy định cách tính và nguồn giá. Không nhập USD/token vào trường này.
            pa.field("ltv_std", pa.float64(), nullable=False),  # Loan-to-Value chuẩn của tài sản, dùng ngoài E-Mode khi phù hợp. | Khuyến nghị lưu dạng tỷ lệ 0–1 (ví dụ 0.75 = 75%), không lưu 75. Cần ghi rõ quy tắc ch...
            pa.field("lt_std", pa.float64(), nullable=False),  # Liquidation Threshold chuẩn của tài sản. | Khuyến nghị dạng tỷ lệ 0–1. Dùng đúng cấu hình có hiệu lực tại snapshot.
            pa.field("liq_bonus_std", pa.float64(), nullable=False),  # Mức thưởng thanh lý tiêu chuẩn. | Cần thống nhất biểu diễn là premium (ví dụ 0.05 = 5%) hay multiplier (1.05 = 105%). Khuyến nghị lưu premium fraction v...
            pa.field("lt_emode", pa.float64(), nullable=False),  # Liquidation Threshold áp dụng theo E-Mode nếu tài sản và vị thế đủ điều kiện. | Code bắt buộc không Null nhưng chưa định nghĩa giá trị khi E-Mode không ...
            pa.field("liq_bonus_emode", pa.float64(), nullable=False),  # Mức thưởng thanh lý áp dụng trong E-Mode nếu có. | Cùng quy ước đơn vị với liq_bonus_std. Cần quyết định cách biểu diễn khi E-Mode không áp dụng.
            pa.field("dex_depth_2pct_usd", pa.float64(), nullable=False),  # Ước lượng độ sâu thanh khoản DEX trong phạm vi tác động giá 2%, tính theo USD. | Ghi rõ DEX/pool, phía mua-bán, block và phương pháp tính. Không xem đây...
            pa.field("lambda_impact", pa.float64(), nullable=False),  # Hệ số lambda trong hàm ước lượng price impact. | Là tham số mô hình; cần lưu nguồn hiệu chỉnh/phiên bản mô hình. Không nhầm với dữ liệu quan sát trực tiếp.
            pa.field("gamma_exponent", pa.float64(), nullable=False),  # Số mũ gamma trong hàm tác động giá phi tuyến. | Là tham số mô hình; dùng nhất quán giữa calibration và simulation.
        ]),
        "dim_asset_config",
    )

    # Bảng 2: fact_wallet_positions (PK: snapshot_block, protocol_id, wallet_address, token_address)
    s2 = _attach_key_metadata(
        pa.schema([
            pa.field("snapshot_block", pa.int64(), nullable=False),  # PK & FK | Số block của snapshot/record; cần khớp với block được dùng để nối các bảng liên quan.
            pa.field("protocol_id", pa.string(), nullable=False),  # PK & FK | Mã protocol/market theo danh mục định danh chung của nhóm; phải nhất quán giữa các bảng.
            pa.field("wallet_address", pa.string(), nullable=False),  # PK | Địa chỉ tài khoản/ví trên chain; chuẩn hóa chữ thường, không tự suy ra chủ sở hữu kinh tế.
            pa.field("token_address", pa.string(), nullable=False),  # PK & FK -> Bảng 1 | Địa chỉ contract token; dùng làm khóa định danh tài sản, chuẩn hóa chữ thường.
            pa.field("is_collateral_enabled", pa.bool_(), nullable=False),  # Cho biết token có được bật làm tài sản thế chấp cho vị thế hay không. | True/False; không suy ra chỉ từ việc ví đang nắm giữ token.
            pa.field("emode_category", pa.int32(), nullable=False),  # E-Mode category áp dụng cho ví tại snapshot. | Thống nhất quy tắc khi không dùng E-Mode.
            pa.field("is_isolated_collateral", pa.bool_(), nullable=False),  # Cho biết vị thế/token có liên quan đến chế độ isolated collateral hay không. | Định nghĩa theo quy tắc của protocol tại snapshot; không tự suy đoán khi ...
            pa.field("collateral_amount_norm", pa.float64(), nullable=False),  # Lượng token được tính làm collateral, sau khi chuẩn hóa decimals. | Đơn vị token; phải không âm và không phải amount_raw.
            pa.field("collateral_usd_t0", pa.float64(), nullable=False),  # Giá trị USD của collateral tại snapshot. | Thông thường = collateral_amount_norm × price_usd_t0, sau khi tính đúng số dư đủ điều kiện làm collateral.
            pa.field("debt_amount_norm", pa.float64(), nullable=False),  # Lượng token nợ sau khi chuẩn hóa decimals. | Đơn vị token nợ; cần thống nhất cách tính phần lãi đã tích lũy tại snapshot.
            pa.field("debt_usd_t0", pa.float64(), nullable=False),  # Giá trị USD của khoản nợ tại snapshot. | Thông thường = debt_amount_norm × price_usd_t0.
            pa.field("effective_lt", pa.float64(), nullable=False),  # Liquidation threshold hiệu dụng liên quan đến vị thế/cạnh; thống nhất tỷ lệ 0–1 và quy tắc E-Mode.
            pa.field("effective_liq_bonus", pa.float64(), nullable=False),  # Liquidation bonus hiệu dụng cho vị thế/token. | Dùng cùng biểu diễn với liq_bonus_std và liq_bonus_emode; định nghĩa premium hay multiplier phải nhất quán.
        ]),
        "fact_wallet_positions",
    )

    # Bảng 3: fact_wallet_summary (PK: snapshot_block, protocol_id, wallet_address)
    s3 = _attach_key_metadata(
        pa.schema([
            pa.field("snapshot_block", pa.int64(), nullable=False),  # PK & FK | Số block của snapshot/record; cần khớp với block được dùng để nối các bảng liên quan.
            pa.field("protocol_id", pa.string(), nullable=False),  # PK & FK | Mã protocol/market theo danh mục định danh chung của nhóm; phải nhất quán giữa các bảng.
            pa.field("wallet_address", pa.string(), nullable=False),  # PK & FK -> Bảng 2 | Địa chỉ tài khoản/ví trên chain; chuẩn hóa chữ thường, không tự suy ra chủ sở hữu kinh tế.
            pa.field("emode_category", pa.int32(), nullable=False),  # E-Mode category áp dụng cho ví tại snapshot. | Thống nhất quy tắc khi không dùng E-Mode.
            pa.field("total_collateral_usd", pa.float64(), nullable=False),  # Tổng giá trị USD của collateral đủ điều kiện được ghi nhận. | Nên bằng tổng collateral_usd_t0 theo token, không cộng số dư token không được bật làm coll...
            pa.field("total_debt_usd", pa.float64(), nullable=False),  # Tổng giá trị USD của các khoản nợ. | Nên bằng tổng debt_usd_t0 theo token.
            pa.field("weighted_collateral_lt_usd", pa.float64(), nullable=False),  # Tổng giá trị collateral USD có trọng số liquidation threshold. | Thông thường = Σ(collateral_usd × effective_lt); đơn vị USD tương đương.
            pa.field("net_equity_usd", pa.float64(), nullable=False),  # Vốn tự có ròng xấp xỉ: collateral USD trừ debt USD. | Có thể âm. Chỉ là thước đo theo định nghĩa của nhóm, chưa nhất thiết là giá trị có thể thu hồi sau...
            pa.field("equity_bucket", pa.string(), nullable=False),  # Nhãn phân nhóm vốn tự có của ví. | Danh mục nhãn chưa được định nghĩa trong code; thống nhất ngưỡng và tên bucket trước khi tạo dữ liệu.
            pa.field("effective_leverage", pa.float64(), nullable=False),  # Hệ số đòn bẩy hiệu dụng theo quy tắc của dự án. | Code không quy định rõ công thức và xử lý equity <= 0. Cần thống nhất mẫu số; nên tránh Inf/giá trị gi...
            pa.field("hf_calculated_t0", pa.float64(), nullable=False),  # Health Factor tự tính tại snapshot. | Thông thường = weighted_collateral_lt_usd / total_debt_usd. Cần quy ước khi total_debt_usd = 0.
            pa.field("hf_reported_t0", pa.float64(), nullable=True),  # Có thể Null nếu contract không trả về | Health Factor do protocol/contract/indexer báo cáo nếu có. | Dùng để đối chiếu với hf_calculated_t0; có thể Null khi nguồn không cung cấp.
            pa.field("dist_to_liq_pct", pa.float64(), nullable=False),  # Biên độ giảm giá ước lượng trước khi chạm HF = 1. | Công thức đơn giản thường là (1 − 1/HF)×100; chỉ là xấp xỉ nếu giả định mọi collateral giảm cùng tỷ ...
            pa.field("dominant_collateral_symbol", pa.string(), nullable=False),  # Symbol của tài sản đóng góp giá trị collateral lớn nhất. | Nên định nghĩa theo collateral_usd_t0 đủ điều kiện; quy tắc xử lý hòa và không có collateral ...
            pa.field("dominant_debt_symbol", pa.string(), nullable=False),  # Symbol của tài sản có giá trị nợ lớn nhất. | Nên định nghĩa theo debt_usd_t0; quy tắc xử lý không có debt cần thống nhất.
            pa.field("is_recursive_loop", pa.bool_(), nullable=False),  # Cờ cho biết ví được phân loại có hành vi Recursive Looping. | Là kết quả phân loại từ chuỗi giao dịch, không phải trường trực tiếp của hợp đồng. Phải tà...
            pa.field("is_multi_collateral", pa.bool_(), nullable=False),  # Cờ cho biết ví dùng nhiều hơn một tài sản collateral. | Định nghĩa theo số token collateral đủ điều kiện, không theo số token đơn thuần đang nắm giữ.
            pa.field("is_cross_protocol", pa.bool_(), nullable=False),  # Cờ cho biết địa chỉ có hoạt động/vị thế ở nhiều protocol. | Trong summary theo protocol, cờ này phải được tính bằng dữ liệu cross-protocol. Địa chỉ giốn...
        ]),
        "fact_wallet_summary",
    )

    # Bảng 4: graph_network_edges (PK: snapshot_block, edge_id)
    s4 = _attach_key_metadata(
        pa.schema([
            pa.field("snapshot_block", pa.int64(), nullable=False),  # PK | Số block của snapshot/record; cần khớp với block được dùng để nối các bảng liên quan.
            pa.field("edge_id", pa.string(), nullable=False),  # PK | ID duy nhất của cạnh trong snapshot. | Tạo ổn định từ node nguồn, node đích, edge_type và protocol nếu muốn tái lập kết quả; phải không trùng trong snap...
            pa.field("source_node_id", pa.string(), nullable=False),  # FK logic (wallet: / token: / protocol:) | ID nút nguồn của cạnh. | Khuyến nghị tiền tố rõ ràng: wallet:0x…, token:0x…, protocol:aave_v3_ethereum.
            pa.field("source_layer", pa.string(), nullable=False),  # Lớp mạng của nút nguồn. | Danh mục thống nhất: wallet, token, protocol; tránh viết khác nhau như Wallet/wallet.
            pa.field("target_node_id", pa.string(), nullable=False),  # FK logic | ID nút đích của cạnh. | Dùng cùng quy tắc với source_node_id.
            pa.field("target_layer", pa.string(), nullable=False),  # Lớp mạng của nút đích. | Phải phù hợp với tiền tố của target_node_id.
            pa.field("edge_type", pa.string(), nullable=False),  # Loại quan hệ được biểu diễn bởi cạnh. | Ví dụ collateral_exposure, debt_exposure, wallet_protocol; đây là ví dụ đề xuất, cần enum chung.
            pa.field("protocol_id", pa.string(), nullable=False),  # Mã protocol/market theo danh mục định danh chung của nhóm; phải nhất quán giữa các bảng.
            pa.field("weight_usd_t0", pa.float64(), nullable=False),  # Trọng số cạnh theo giá trị phơi nhiễm tại snapshot, tính bằng USD. | Không âm; ghi rõ cách gộp nếu cạnh có nhiều vị thế/tài sản.
            pa.field("effective_lt", pa.float64(), nullable=True),  # Liquidation threshold hiệu dụng liên quan đến vị thế/cạnh; thống nhất tỷ lệ 0–1 và quy tắc E-Mode.
        ]),
        "graph_network_edges",
    )

    # Bảng 5: out_simulation_results
    s5 = _attach_key_metadata(
        pa.schema([
            pa.field("run_id", pa.string(), nullable=False),  # PK | Mã định danh duy nhất của lượt chạy mô phỏng. | Nên sinh UUID hoặc mã gồm model_version + thời điểm + seed; cùng một lượt chạy Monte Carlo nên dùng cùng...
            pa.field("snapshot_block", pa.int64(), nullable=False),  # FK -> Bảng 1, 3 | Số block của snapshot/record; cần khớp với block được dùng để nối các bảng liên quan.
            pa.field("question_id", pa.string(), nullable=False),  # PK | Câu hỏi nghiên cứu mà kết quả trả lời. | Chuẩn hóa thành Q1, Q2, Q3.
            pa.field("scenario_name", pa.string(), nullable=False),  # PK | Tên kịch bản stress. | Ví dụ: stETH_depeg_mild, stETH_depeg_severe; phải lưu bảng giải thích các mức sốc và giả định.
            pa.field("cf_rule", pa.string(), nullable=False),  # PK | Mã quy tắc Close Factor được áp dụng. | Ví dụ baseline, fixed_50pct, early_100pct; các mã phải map tới tham số đầy đủ trong cấu hình thí nghiệm.
            pa.field("cascade_round", pa.int32(), nullable=False),  # PK | Vòng lặp thanh lý trong chuỗi lây lan. | Quy ước vòng 0/1 phải thống nhất. Không trộn vòng khởi tạo với vòng đầu tiên thanh lý.
            pa.field("protocol_id", pa.string(), nullable=False),  # PK | Mã protocol/market theo danh mục định danh chung của nhóm; phải nhất quán giữa các bảng.
            pa.field("segment_group", pa.string(), nullable=False),  # PK | Nhóm ví/vị thế được tổng hợp. | Ví dụ looping, non_looping, all, collateral_wsteth. Quy tắc lọc nhóm cần ghi lại.
            pa.field("round_liq_vol_usd", pa.float64(), nullable=False),  # Tổng giá trị nợ được thanh lý trong riêng vòng hiện tại. | Đơn vị USD; cần xác định là debt repaid USD, không phải collateral seized USD.
            pa.field("cum_liq_vol_usd", pa.float64(), nullable=False),  # Tổng khối lượng nợ thanh lý tích lũy từ vòng đầu đến vòng hiện tại. | Thông thường = tổng round_liq_vol_usd từ đầu chuỗi đến vòng này.
            pa.field("pos_bad_debt_usd", pa.float64(), nullable=False),  # Giá trị bad debt tương ứng hàng kết quả. | Tên trường gợi ý cấp vị thế nhưng grain bảng là nhóm; cần thống nhất đây là tổng bad debt của nhóm trong kịch...
            pa.field("bad_debt_ratio_pct", pa.float64(), nullable=False),  # Tỷ lệ bad debt so với dư nợ ban đầu của nhóm. | Lưu theo đơn vị phần trăm 0–100; mẫu số phải được cố định (dư nợ ban đầu của nhóm, không thay đổi sau mỗ...
            pa.field("equity_norm_loss_pct", pa.float64(), nullable=True),  # Bad debt chuẩn hóa theo vốn tự có ban đầu. | Cần công bố cách xử lý tổng vốn tự có bằng 0 hoặc âm; có thể không xác định và lưu Null.
            pa.field("prob_bad_debt_pct", pa.float64(), nullable=True),  # Xác suất phát sinh bad debt qua các lần chạy/kịch bản tương ứng. | Lưu phần trăm 0–100. Không tính từ một lần chạy duy nhất; cần số mẫu và điều kiện sự ...
            pa.field("tail_loss_es99_usd", pa.float64(), nullable=True),  # Expected Shortfall 99% của phân phối tổn thất. | Đơn vị USD; cần tính từ nhiều mẫu mô phỏng, ghi rõ phân phối tổn thất, cách xử lý VaR tie và số lần chạy.
            pa.field("dex_price_impact_pct", pa.float64(), nullable=False),  # Mức tác động giá DEX trong vòng/kết quả. | Lưu phần trăm; nếu một hàng tổng hợp nhiều token, phải quy định là mức tối đa, bình quân, hay chỉ số có trọng...
            pa.field("seq_liquidated_count", pa.int32(), nullable=False),  # Số vị thế bị cuốn vào thanh lý nối tiếp. | Cần định nghĩa đếm duy nhất theo ví, theo vị thế, hay theo lần thanh lý; tránh cộng trùng cùng ví qua nhiều v...
            pa.field("cascade_amp_factor", pa.float64(), nullable=False),  # Tỷ lệ tổng khối lượng thanh lý cả chuỗi so với vòng đầu tiên. | Tổng thanh lý / thanh lý vòng đầu. Nếu volume vòng đầu bằng 0, cần quy ước Null/NA thay ...
            pa.field("liq_freeze_vol_usd", pa.float64(), nullable=False),  # Dư nợ dưới HF < 1 nhưng không có động cơ kinh tế để liquidator thanh lý theo giả định mô hình. | Là ước lượng phụ thuộc reward, gas, slippage và chi phí...
            pa.field("healthy_to_liq_rate_pct", pa.float64(), nullable=True),  # Tỷ lệ vị thế ban đầu khỏe mạnh chuyển sang đủ điều kiện thanh lý sau stress. | Cần định nghĩa ngưỡng ban đầu “khỏe mạnh” (ví dụ HF ≥ 1.05) và mẫu số nhó...
            pa.field("cross_protocol_contagion_pct", pa.float64(), nullable=True),  # Chỉ số tỷ lệ lây lan sang protocol khác. | Cần chọn mẫu số thống nhất: dư nợ ban đầu, số vị thế, hoặc khối lượng nợ thanh lý; không đổi cách tính giữa c...
        ]),
        "out_simulation_results",
    )

    # Bảng 6: fact_historical_liquidations (PK: tx_hash, log_index)
    s6 = _attach_key_metadata(
        pa.schema([
            pa.field("tx_hash", pa.string(), nullable=False),  # PK | Hash của giao dịch chứa sự kiện thanh lý. | Lưu chuẩn hex; khi mở rộng đa chain, cân nhắc thêm chain_id/protocol_id vào khóa.
            pa.field("log_index", pa.int32(), nullable=False),  # PK | Vị trí log sự kiện trong transaction/block theo quy ước indexer. | Phải dùng cùng một hệ thống indexing; log_index phân biệt nhiều event trong cùng giao...
            pa.field("event_block", pa.int64(), nullable=False),  # Block xảy ra sự kiện thanh lý. | Dùng để truy ngược giao dịch và lấy giá/config tương ứng.
            pa.field("event_timestamp", pa.timestamp("ns", tz="UTC"), nullable=False),  # Thời điểm UTC của block chứa sự kiện. | Lấy từ block timestamp; không nhầm với thời điểm crawler ghi dữ liệu.
            pa.field("protocol_id", pa.string(), nullable=False),  # FK | Mã protocol/market theo danh mục định danh chung của nhóm; phải nhất quán giữa các bảng.
            pa.field("wallet_address", pa.string(), nullable=False),  # FK -> Bảng 3 | Địa chỉ tài khoản/ví trên chain; chuẩn hóa chữ thường, không tự suy ra chủ sở hữu kinh tế.
            pa.field("liquidator_address", pa.string(), nullable=False),  # Địa chỉ liquidator thực hiện thanh lý. | Chuẩn hóa chữ thường; có thể là bot/contract, không nhất thiết là cá nhân.
            pa.field("collateral_token_address", pa.string(), nullable=False),  # FK -> Bảng 1 | Địa chỉ token collateral bị thu giữ. | Code kiểm tra token này có trong dim_asset_config cùng protocol, nhưng không đối chiếu snapshot hoặc block event.
            pa.field("debt_token_address", pa.string(), nullable=False),  # FK -> Bảng 1 | Địa chỉ token nợ được hoàn trả trong sự kiện. | Schema ghi chú FK nhưng code chưa kiểm tra token này tồn tại trong dim_asset_config.
            pa.field("debt_repaid_usd", pa.float64(), nullable=False),  # Giá trị USD của phần nợ được hoàn trả trong thanh lý. | Cần nêu cách quy đổi lượng debt token sang USD và nguồn giá/thời điểm dùng.
            pa.field("collateral_seized_usd", pa.float64(), nullable=False),  # Giá trị USD của collateral chuyển cho liquidator. | Cần nêu nguồn giá quy đổi, có thể khác giá bán thực tế sau slippage.
        ]),
        "fact_historical_liquidations",
    )

    schemas = {
        "dim_asset_config": s1,
        "fact_wallet_positions": s2,
        "fact_wallet_summary": s3,
        "graph_network_edges": s4,
        "out_simulation_results": s5,
        "fact_historical_liquidations": s6,
    }

    for t_name, schema in schemas.items():
        path = TABLE_KEY_CONTRACTS[t_name]["path"]
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

        if os.path.exists(path):
            existing_schema = pq.read_schema(path)
            _check_schema_compatible(existing_schema, schema, t_name)
            if not overwrite:
                print(f"[SKIP] {t_name:<30} | File da ton tai; da xac nhan schema, khong ghi de. ")
                continue

            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_path = f"{path}.bak_{stamp}"
            shutil.copy2(path, backup_path)
            print(f"[BACKUP] {path} -> {backup_path}")

        empty_tbl = pa.Table.from_pylist([], schema=schema)
        _write_parquet_atomically(empty_tbl, path)
        pks = TABLE_KEY_CONTRACTS[t_name]["primary_keys"]
        print(f"[INIT] {t_name:<30} | PK: {pks}")


# ==============================================================================
# 3. HÀM KIỂM TRA KHÓA KẾT NỐI BẮT BUỘC TRƯỚC KHI LƯU (DÀNH CHO THÀNH VIÊN)
# ==============================================================================
def save_table_with_key_check(df: pd.DataFrame, table_name: str, mode: str = "append"):
    """Validate va luu batch vao bang Parquet ma khong vo tinh ghi de du lieu.

    Parameters
    ----------
    df:
        DataFrame moi can luu; phai co dung bo cot theo schema.
    table_name:
        Ten mot trong 6 bang da dinh nghia trong TABLE_KEY_CONTRACTS.
    mode:
        - "append" (mac dinh): them dong moi; tu choi neu PK trung voi du lieu cu.
        - "upsert": thay the dong cu co cung PK bang dong moi, giu cac dong khac.
        - "replace_partition": thay toan bo partition duoc dinh nghia trong
          REPLACE_PARTITION_COLUMNS, sau do ghi batch moi. Batch phai chua day du
          du lieu cua partition can thay the.

    Luu y: moi lan ghi deu doc va ghi lai file Parquet don. Chi nen dung mot writer
    cho moi bang tai mot thoi diem; khong cho nhieu thanh vien/tiến trinh ghi dong thoi.
    """
    if table_name not in TABLE_KEY_CONTRACTS:
        raise ValueError(f"Ten bang khong hop le: {table_name}")
    if mode not in {"append", "upsert", "replace_partition"}:
        raise ValueError("mode phai la 'append', 'upsert' hoac 'replace_partition'.")

    contract = TABLE_KEY_CONTRACTS[table_name]
    target_path = contract["path"]
    pks = contract["primary_keys"]
    fks = contract["foreign_keys"]
    addr_cols = contract["address_cols"]

    if not os.path.exists(target_path):
        raise FileNotFoundError(
            f"Chua co bang '{target_path}'. Hay chay init_empty_tables_with_keys() "
            "hoac khoi tao schema dung truoc khi luu du lieu."
        )

    existing_schema = pq.read_schema(target_path)
    # Xac nhan file mang metadata cua dung bang va dung bo PK/FK contract.
    schema_meta = existing_schema.metadata or {}
    stored_table_name = schema_meta.get(b"table_name", b"").decode("utf-8")
    stored_pks_raw = schema_meta.get(b"primary_keys")
    stored_fks_raw = schema_meta.get(b"foreign_keys")
    if stored_table_name != table_name or stored_pks_raw is None or stored_fks_raw is None:
        raise ValueError(
            f"Metadata trong '{target_path}' khong xac nhan duoc bang/PK/FK mong doi. "
            "Hay tao/kiem tra schema bang truoc; khong luu du lieu vao file nay."
        )
    try:
        stored_pks = json.loads(stored_pks_raw.decode("utf-8"))
        stored_fks = json.loads(stored_fks_raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"Metadata PK/FK cua '{table_name}' khong doc duoc: {exc}") from exc
    if stored_pks != pks or stored_fks != fks:
        raise ValueError(
            f"Metadata PK/FK trong file '{table_name}' khong khop TABLE_KEY_CONTRACTS. "
            "Can kiem tra thay doi schema/contract truoc khi tiep tuc."
        )
    expected_cols = existing_schema.names
    missing_cols = [c for c in expected_cols if c not in df.columns]
    extra_cols = [c for c in df.columns if c not in expected_cols]
    if missing_cols or extra_cols:
        raise ValueError(
            f"Cot khong khop schema cua '{table_name}'. Cot thieu: {missing_cols}; "
            f"cot thua: {extra_cols}. Khong tu dong bo qua cot thua."
        )

    df_new = df.loc[:, expected_cols].copy()
    if df_new.empty:
        print(f"[NO-OP] '{table_name}': batch co 0 dong; khong thay doi file.")
        return

    # 1. Kiem tra tat ca cot nullable=False, khong chi rieng PK.
    for field in existing_schema:
        if not field.nullable and df_new[field.name].isna().any():
            raise ValueError(
                f"[NULL KHONG DUOC PHEP] Bang '{table_name}', cot '{field.name}' "
                "co gia tri Null/NaN nhung schema quy dinh nullable=False."
            )

    # 2. Chuan hoa dia chi EVM va kiem tra du 42 ky tu hex (0x + 40 ky tu).
    for col in addr_cols:
        if col not in df_new.columns:
            raise ValueError(f"[THIEU COT DIA CHI] Bang '{table_name}' thieu cot '{col}'.")
        df_new[col] = df_new[col].astype(str).str.lower().str.strip()
        valid_addr = df_new[col].str.fullmatch(r"0x[0-9a-f]{40}", na=False)
        if not valid_addr.all():
            bad_sample = df_new.loc[~valid_addr, col].iloc[0]
            raise ValueError(
                f"[DIA CHI KHONG HOP LE] Bang '{table_name}', cot '{col}' co gia tri "
                f"khong dung dinh dang EVM (0x + 40 ky tu hex): {bad_sample}"
            )

    # 3. Kiem tra PK null va trung lap trong batch moi.
    for pk_col in pks:
        if pk_col not in df_new.columns:
            raise ValueError(f"[THIEU PK] Bang '{table_name}' thieu cot khoa chinh '{pk_col}'.")
        if df_new[pk_col].isna().any():
            raise ValueError(f"[PK NULL] Bang '{table_name}', cot '{pk_col}' co Null/NaN.")

    dup_mask = df_new.duplicated(subset=pks, keep=False)
    if dup_mask.any():
        sample = df_new.loc[dup_mask, pks].head(3).to_dict(orient="records")
        raise ValueError(
            f"[PK TRUNG TRONG BATCH] Bang '{table_name}' co dong trung khoa chinh {pks}. "
            f"Vi du: {sample}"
        )

    # 4. Kiem tra FK voi bang cha. Neu bang cha chua co du lieu thi dung lai,
    # khong im lang bo qua FK nhu ban code cu.
    for fk in fks:
        parent_path = fk["parent_path"]
        parent_name = fk["parent_table"]
        if not os.path.exists(parent_path):
            raise FileNotFoundError(
                f"[THIEU BANG CHA] '{table_name}' can '{parent_name}' tai '{parent_path}'. "
                "Hay luu bang cha truoc."
            )
        parent_df = pd.read_parquet(parent_path)
        if parent_df.empty and len(df_new) > 0:
            raise ValueError(
                f"[BANG CHA RONG] '{parent_name}' dang rong; khong the xac minh FK cua '{table_name}'. "
                "Hay nap/lưu bang cha truoc khi luu bang con."
            )

        left_cols, right_cols = [], []
        for col_map in fk["join_columns"]:
            if "->" in col_map:
                left_col, right_col = col_map.split("->", 1)
            else:
                left_col = right_col = col_map
            left_cols.append(left_col)
            right_cols.append(right_col)

        missing_fk_cols = [c for c in left_cols if c not in df_new.columns]
        missing_parent_cols = [c for c in right_cols if c not in parent_df.columns]
        if missing_fk_cols or missing_parent_cols:
            raise ValueError(
                f"[FK SCHEMA ERROR] '{table_name}' -> '{parent_name}'. "
                f"Cot thieu ben con: {missing_fk_cols}; cot thieu ben cha: {missing_parent_cols}."
            )

        parent_keys = set(_rows_as_tuples(parent_df.drop_duplicates(subset=right_cols), right_cols))
        child_keys = _rows_as_tuples(df_new, left_cols)
        orphan_mask = [key not in parent_keys for key in child_keys]
        if any(orphan_mask):
            examples = [dict(zip(left_cols, key)) for key, is_orphan in zip(child_keys, orphan_mask) if is_orphan][:3]
            raise ValueError(
                f"[FK ORPHAN] Bang '{table_name}' co {sum(orphan_mask)} khoa khong ton tai "
                f"trong bang cha '{parent_name}' theo cap cot {left_cols} -> {right_cols}. "
                f"Vi du: {examples}"
            )

    # 5. Doc du lieu hien co; chon cach hop nhat minh bach.
    existing_df = pd.read_parquet(target_path)
    if list(existing_df.columns) != expected_cols:
        raise ValueError(
            f"Cot trong file hien tai '{table_name}' khong khop schema. "
            "Khong ghi du lieu cho den khi sua xong schema/file."
        )

    if mode == "append":
        combined_df = pd.concat([existing_df, df_new], ignore_index=True)
    elif mode == "upsert":
        # Ghi de dung cac dong co PK trung; cac dong khac duoc giu nguyen.
        incoming_keys = set(_rows_as_tuples(df_new, pks))
        old_keys = _rows_as_tuples(existing_df, pks) if not existing_df.empty else []
        keep_mask = [key not in incoming_keys for key in old_keys]
        existing_kept = existing_df.loc[keep_mask]
        combined_df = pd.concat([existing_kept, df_new], ignore_index=True)
    else:  # replace_partition
        if table_name not in REPLACE_PARTITION_COLUMNS:
            raise ValueError(
                f"replace_partition chua duoc dinh nghia an toan cho '{table_name}'. "
                "Hay dung append/upsert hoac thiet ke pham vi partition rieng."
            )
        partition_cols = REPLACE_PARTITION_COLUMNS[table_name]
        partition_keys = set(_rows_as_tuples(df_new, partition_cols))
        old_partition_keys = _rows_as_tuples(existing_df, partition_cols) if not existing_df.empty else []
        keep_mask = [key not in partition_keys for key in old_partition_keys]
        existing_kept = existing_df.loc[keep_mask]
        combined_df = pd.concat([existing_kept, df_new], ignore_index=True)

    # Kiem tra tinh duy nhat tren TOAN BO ket qua sau khi hop nhat.
    dup_all = combined_df.duplicated(subset=pks, keep=False)
    if dup_all.any():
        sample = combined_df.loc[dup_all, pks].head(5).to_dict(orient="records")
        raise ValueError(
            f"[PK TRUNG SAU KHI HOP NHAT] Khong luu '{table_name}'. Mode='{mode}' van tao khoa trung. "
            f"Khoa vi du: {sample}"
        )

    # 6. Chuyen ve Arrow dung schema/metadata da dinh nghia va ghi file tam.
    combined_ordered = combined_df.loc[:, expected_cols]
    try:
        arrow_table = pa.Table.from_pandas(
            combined_ordered,
            schema=existing_schema,
            preserve_index=False,
            safe=True,
        )
    except Exception as exc:
        raise ValueError(
            f"Khong the chuyen DataFrame sang schema Parquet cua '{table_name}'. "
            f"Kiem tra kieu du lieu/cac gia tri: {exc}"
        ) from exc

    _write_parquet_atomically(arrow_table, target_path)
    print(
        f"[PASSED & SAVED] '{table_name}' | mode={mode} | batch={len(df_new)} dong | "
        f"tong sau luu={len(combined_df)} dong | PK={pks}"
    )


if __name__ == "__main__":
    # Mac dinh an toan: tao cac file chua ton tai, KHONG xoa file da co.
    # Chi truyen overwrite=True khi chu dong can reset bang va da co backup/chap thuan.
    init_empty_tables_with_keys()
