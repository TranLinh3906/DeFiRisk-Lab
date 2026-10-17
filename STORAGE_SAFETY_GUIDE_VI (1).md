# Hướng dẫn lưu dữ liệu Parquet an toàn cho dự án DeFi Lending Stress Test

## 2. Phiên bản code an toàn

Dùng `schema_storage_safe.py`. File giữ nguyên 6 bảng, tên cột, kiểu dữ liệu, PK/FK contract và đường dẫn; chỉ thay đổi cách khởi tạo/lưu file.

> Nên sao lưu toàn bộ thư mục `data/processed` và `outputs/tables` trước khi tích hợp script mới. Chạy thử trên bản sao trước khi sử dụng với dữ liệu duy nhất của nhóm.

## 3. Khởi tạo lần đầu và các lần sau

```python
from schema_storage_safe import init_empty_tables_with_keys

init_empty_tables_with_keys()
```

Hành vi mặc định:

- Chưa có file: tạo file Parquet rỗng theo schema đã định nghĩa.
- Đã có file: kiểm tra schema/metadata rồi bỏ qua, không ghi đè dữ liệu.
- Schema hiện có không khớp với code: báo lỗi để nhóm xử lý có chủ đích; không tự sửa hoặc ghi đè.

**Không dùng** `init_empty_tables_with_keys(overwrite=True)` khi chưa định xóa dữ liệu. Chế độ này tạo bản sao `.bak_<UTC timestamp>` nhưng vẫn đặt lại bảng thành rỗng.

## 4. Ba chế độ lưu dữ liệu

### `mode="append"` — thêm batch mới, mặc định và an toàn nhất

```python
save_table_with_key_check(df_new, "dim_asset_config")
```

- Đọc dữ liệu cũ.
- Kiểm tra PK của batch mới.
- Kiểm tra PK có trùng với dữ liệu cũ sau khi ghép không.
- Nếu trùng PK: dừng và báo lỗi, không ghi file.
- Nếu không trùng: ghép cũ + mới và ghi qua file tạm rồi mới thay file chính.

Dùng cho batch thực sự bổ sung các khóa mới.

### `mode="upsert"` — cập nhật những khóa cụ thể

```python
save_table_with_key_check(df_correction, "fact_historical_liquidations", mode="upsert")
```

- Nếu PK của dòng mới đã tồn tại, dòng cũ có đúng PK đó được thay bằng dòng mới.
- Các dòng cũ không xuất hiện trong batch vẫn được giữ.

Phù hợp khi chạy lại một số liquidation event để sửa/cập nhật cùng `tx_hash + log_index`. **Không** thay thế được việc làm mới toàn bộ snapshot vì các dòng cũ không xuất hiện trong batch vẫn còn.

### `mode="replace_partition"` — thay thế cả phạm vi snapshot/run

Ví dụ:

```python
save_table_with_key_check(df_asset_snapshot, "dim_asset_config", mode="replace_partition")
save_table_with_key_check(df_positions_snapshot, "fact_wallet_positions", mode="replace_partition")
save_table_with_key_check(df_wallet_summary, "fact_wallet_summary", mode="replace_partition")
save_table_with_key_check(df_sim_result, "out_simulation_results", mode="replace_partition")
```

Phạm vi thay thế được định nghĩa trong `REPLACE_PARTITION_COLUMNS`:

- `dim_asset_config`: `snapshot_block + protocol_id`
- `fact_wallet_positions`: `snapshot_block + protocol_id`
- `fact_wallet_summary`: `snapshot_block + protocol_id`
- `graph_network_edges`: `snapshot_block + protocol_id`
- `out_simulation_results`: `run_id`

Dùng chế độ này **chỉ khi DataFrame đầu vào chứa đầy đủ mọi dòng của partition đó**. Ví dụ, nếu đầu vào chỉ có 10 ví trong khi snapshot của protocol có 1.000 ví, `replace_partition` sẽ xóa các dòng còn lại của snapshot đó rồi thay bằng 10 dòng.

`fact_historical_liquidations` không bật `replace_partition` vì thay thế theo block chỉ an toàn khi batch được đảm bảo chứa đầy đủ mọi liquidation event của những block cần thay. Với bảng này, dùng `append` cho event mới và `upsert` cho các event có PK đã tồn tại.

## 5. Thứ tự lưu để khóa ngoại được kiểm tra

1. `dim_asset_config`
2. `fact_wallet_positions`
3. `fact_wallet_summary`
4. `graph_network_edges` (code hiện không khai báo FK tự động cho bảng này)
5. `fact_historical_liquidations` (cần `dim_asset_config` đã có các token tương ứng)
6. `out_simulation_results` (contract hiện tại chưa khai báo FK tự động dù cột `snapshot_block` có chú thích FK)

Bảng con chỉ lưu sau khi bảng cha đã có dữ liệu. Phiên bản an toàn dừng nếu bảng cha liên quan bị thiếu hoặc rỗng, thay vì âm thầm bỏ qua kiểm tra.

## 6. Ví dụ dùng trong pipeline

```python
from schema_storage_safe import (
    init_empty_tables_with_keys,
    save_table_with_key_check,
)

# Chạy ở đầu pipeline; không ghi đè các file hiện có.
init_empty_tables_with_keys()

# Mỗi DataFrame phải có đúng tên cột và kiểu giá trị theo schema.
save_table_with_key_check(df_asset_config, "dim_asset_config", mode="append")
save_table_with_key_check(df_wallet_positions, "fact_wallet_positions", mode="append")
save_table_with_key_check(df_wallet_summary, "fact_wallet_summary", mode="append")
save_table_with_key_check(df_graph_edges, "graph_network_edges", mode="append")
save_table_with_key_check(df_historical_liquidations, "fact_historical_liquidations", mode="append")
save_table_with_key_check(df_simulation_results, "out_simulation_results", mode="append")
```

Tên biến `df_asset_config`, v.v. là tên minh họa; thay bằng DataFrame thực tế của nhóm.

## 7. Quy tắc làm việc nhóm

- Chỉ một process được ghi vào cùng một file Parquet tại một thời điểm. Atomic replace giảm nguy cơ file ghi dở nhưng không giải quyết tranh chấp giữa hai writer đồng thời.
- Tốt nhất mỗi thành viên xuất batch riêng vào thư mục staging; một integration script duy nhất kiểm tra rồi nhập batch tuần tự.
- Trước khi chạy lại pipeline, quyết định rõ là `append`, `upsert` hay `replace_partition`; không chọn chế độ theo cảm tính.
- `append` là mặc định. Khi báo trùng PK, hãy kiểm tra xem đó là dữ liệu trùng, dữ liệu cần sửa hay snapshot cần chạy lại hoàn chỉnh.
- Giữ backup và ghi nhật ký: `run_id`, `snapshot_block`, protocol, thời gian chạy, số dòng đầu vào, số dòng sau lưu, chế độ lưu và phiên bản code.

## 8. Giới hạn hiện tại cần biết

- Kiểm tra FK chỉ áp dụng cho các quan hệ được khai báo trong `TABLE_KEY_CONTRACTS`. Một số chú thích trong schema nói “FK” nhưng chưa được khai báo trong danh sách `foreign_keys`, ví dụ `snapshot_block` của `out_simulation_results`, địa chỉ ví và `debt_token_address` của `fact_historical_liquidations`. Nếu nhóm muốn kiểm tra chúng, cần bổ sung contract và thống nhất phạm vi join trước.
- Phiên bản này ghi lại toàn bộ file đơn sau khi hợp nhất. Với dữ liệu lớn, nên chuyển sang Parquet partitioned dataset, DuckDB hoặc PostgreSQL thay vì liên tục đọc/ghi một file duy nhất.
- File code đã được kiểm tra cú pháp Python trong môi trường tạo tài liệu; cần chạy kiểm thử tích hợp trên máy dự án với đúng phiên bản pandas/pyarrow trước khi áp dụng dữ liệu thật.
