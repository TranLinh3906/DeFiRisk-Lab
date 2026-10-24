# Kiểm tra code N2/N4 sau pull — HEAD 9b34ff7, 23/10/2026

Phạm vi commit: N4 `de23b7b`, N2 `01c606b`, dọn file tạm `9b34ff7`.
Đọc diff, kiểm cú pháp các file Python thay đổi, chạy test N4 và probe offline.
Không chạy RPC/crawler, không đọc .env, không sửa code hay ghi đè dữ liệu N2/N4.
Pipeline HF được chạy lại với thư mục đầu ra chuyển riêng sang N5.

Bằng chứng lưu tại `outputs/n5_preparation/pull_review_9b34ff7/`.

## Kết quả tổng quát

- Không thấy lỗi cú pháp trong các file Python thay đổi được kiểm tra.
- **22/22 test chính N4 đạt** (HF, Stress, Cliff/Reverse).
- Bản mới sửa oracle priority, chuỗi false, thiếu giá, target âm và tách snapshot
  Cliff/Reverse. Có cải thiện HF thật, nhưng chưa đủ để nghiệm thu.
- Parquet và metadata N2 tại máy vẫn là A/B cũ (A=26179841, B=26194188),
  **chưa có bộ dữ liệu B1/B2** dù config mới đã có A/B1/B2.

## P1 — N2: đổi snapshot chưa xuyên suốt pipeline

### Xuất metadata vẫn cố định A/B

`src/data/write_snapshot_metadata.py:19` còn CHOSEN={A,B}; main tại dòng 36
duyệt bảng này rồi truy cập state.snapshots[label]. Khi state dùng A/B1/B2,
script gặp **KeyError: 'B'**. Đã tái hiện bằng state fixture, ghi đích riêng.

Cần lấy labels/role/block/hash/target/window từ config hiện hành và đối chiếu
state, thay vì giữ CHOSEN và cửa sổ B tháng 10/2026 cố định.

### Normalize vẫn dùng cov['B'] và vòng lặp A/B

`src/data/normalize_aave.py:104` đọc cov['B']; dòng 107 duyệt ('A','B').
Fixture A/B1/B2 tái hiện **KeyError: 'B'**. Đáng chú ý, script đã ghi
borrower_positions và normalization_quality trước khi lỗi ở coverage report:
pipeline có thể để lại các file bàn giao không cùng trạng thái hoàn thành.

Cần duyệt metadata/config động, xác minh các bảng đồng bộ block rồi mới xuất;
nên chuẩn bị toàn bộ kết quả trước khi thay thế bộ file bàn giao.

Fixture ở `n2_normalize_fixture/` dùng block giả 100/200/300 và dữ liệu mẫu,
không phải dữ liệu lịch sử B1/B2 thật. Không sử dụng các Parquet fixture này làm
đầu vào nghiên cứu. Kết quả probe ở `n2_normalizer_probe.json`.

## P1 — N4: zero-shock đang che bất đồng công thức

`src/models/stress_test.py:202` gán hf_after=hf_before khi mọi shock bằng 0.
Điều này làm zero-shock pass nhưng không bảo đảm HF mart khớp effective_lt,
collateral và số dư dùng cho nhánh stress.

Trên 57.038 dòng ví–snapshot:

| Probe | Sai số HF trước/sau ≥0,005 | Đổi phía HF=1 |
|---|---:|---:|
| Shock = 0 | 0 | Không bộc lộ bất đồng do phép gán |
| ETH shock = −1e-12 | 446 | 118 |

Cần đối chiếu HF cơ sở tính lại từ cùng đầu vào, sửa nguyên nhân hoặc từ chối
baseline không nhất quán; không dùng phép chép kết quả thay cho gate này.

## P1 — N4: E-Mode vẫn thiếu cờ permission và alias collateral của ví

### is_collateral trong membership chưa được áp dụng

Mặc dù đã giữ nhiều category, `health_factor.py:173` lấy membership không có
cột is_collateral; main tại dòng 267 cũng không đưa cột đó vào cấu hình.
Category trùng chưa đủ để áp LT E-Mode nếu token chỉ là borrowable.

Probe: 100 token collateral, 50 token nợ, giá 1, LT chuẩn 0,5, LT E-Mode 0,9,
category 2 trùng nhưng is_collateral=False. Hàm trả HF=1,8; quy tắc adapter
hiện có yêu cầu dùng LT chuẩn, tức HF=1,0.

### collateral_enabled chưa nằm trong alias của hàm HF

`health_factor.py:100` vẫn chỉ xét is_collateral_enabled/coll_flag/
usage_as_collateral. Bảng borrower_positions của N2 dùng **collateral_enabled**.
Khi bỏ qua cờ ví, hàm có thể fallback sang khả năng tài sản được dùng làm
collateral ở cấp reserve, là hai khái niệm khác nhau.

Đối chiếu HF hiện tại so adapter độc lập:

| Chỉ tiêu | A | B |
|---|---:|---:|
| Ví bất đồng phân loại HF=1 | 60 | 58 |
| Ví sai số HF ≥0,005 | 220 | 217 |

Chạy lại pipeline HF hiện tại vào thư mục riêng tái hiện đúng các số lượng này;
không phải chỉ do mart chưa được chạy lại. Không gán mọi bất đồng cho một nguyên
nhân trước khi điều tra chi tiết. Các file `A_disagreements.csv`,
`B_disagreements.csv` và bảng comparison có bằng chứng theo ví.

## P2 — Reverse Stress còn chấp nhận NaN/vô cực ở một số nhánh

`cliff_reverse.py` đã chặn target âm, nhưng nhánh dictionary và chuỗi phần
trăm chưa kiểm hữu hạn/range đầy đủ. Đã tái hiện nhận các target:
`{'usd': NaN}`, `'NaN%'`, `{'pct': infinity}`.

Cần kiểm hữu hạn, không âm và khoảng phần trăm cho mọi nhánh; dùng cùng parser
hoặc cùng bộ validation thay vì kiểm khác nhau giữa kiểu số/dictionary/string.

## N2: các vấn đề tích hợp/phạm vi kiểm tra bổ sung

- `build_candidates.py:21` cần raw tại `data/raw/aave/borrow_events_raw.jsonl`.
  Tại máy hiện file này không có; chỉ có `borrow_events_raw.jsonl` ở gốc. Đây là
  thiếu đầu vào đúng đường dẫn, không phải bằng chứng thuật toán dựng mẫu sai.
  Cần chỉ định/đặt đúng nguồn và xác minh progress tương ứng trước khi chạy.
- fetch_positions mới xuất debt_amount theo variable_debt_aave_raw khi có
  scaled_var, nhưng normalize vẫn xuất variable_debt_amount_norm từ
  variable_debt_raw tại dòng 54. Nếu hai loại debt khác nhau, các cột thành phần
  sẽ không cộng lại bằng debt_amount. Cần chốt semantics, dùng nguồn tương ứng
  hoặc đặt tên rõ và thêm phép kiểm. Chưa có dữ liệu B1/B2 mới để đánh giá thực tế.
- Checkpoint cũ không có scaled_var vẫn fallback sang balanceOf. Không nên
  mặc định kéo code mới là dữ liệu A cũ đã được tái dựng theo công thức mới.
- Không kiểm chứng hash/block và archive state trực tiếp qua RPC trong đợt
  review offline này; các cờ archive_state_readable trong config là khai báo có sẵn.

## Tests N5 cũ cần cập nhật sau thay đổi N4

`test_n4_pending_contracts.py` đang có 5 marker expectedFailure cho hành vi cũ.
Bản mới làm cả 5 test thành **unexpected success**, nên chạy bộ này trả exit 1.
Đây là marker kiểm thử cũ cần rà soát, không phải 5 lỗi chức năng mới của N4.
Riêng test zero-shock cần mạnh hơn bằng kiểm HF tái dựng/tiny shock để bắt vấn
đề phép gán che sai lệch, rồi mới bỏ marker.

## Thứ tự xử lý

1. N2 hoàn tất metadata/normalize dùng config A/B1/B2 và đồng bộ dữ liệu bàn giao.
2. N4 sửa alias cờ collateral và permission E-Mode; kiểm lại với Aave/adapter.
3. N4 bỏ cách dùng phép gán để thay kiểm định zero-shock; kiểm cả shock rất nhỏ.
4. Chặn NaN/vô cực trong target; cập nhật regression tests/N5 theo API mới.
5. Sau khi có dữ liệu B1/B2 đồng bộ, chạy QA theo từng block/cửa sổ mới; không
   đổi nhãn các dữ liệu A/B cũ thành dữ liệu lịch sử.

Lệnh test chính đã chạy:

```powershell
python -B -X utf8 -m pytest tests/test_health_factor.py tests/test_stress.py tests/test_cliff_reverse.py -q -p no:cacheprovider --tb=short
```
