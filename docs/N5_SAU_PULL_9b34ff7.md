# N5 sau pull 9b34ff7 — kết quả thực hiện ngày 23/10/2026

Đã thực hiện bốn nhóm công việc được yêu cầu. Bộ kết quả mới:

`outputs/n5_preparation/n5_postpull_9b34ff7_20261023/`

**69 file đầu ra + manifest SHA-256**; hash đầu vào/code và đầu ra đã được
kiểm lại, không thay đổi nguồn trong lúc chạy. Không sửa code N2/N4 hoặc ghi
đè marts/canonical. Toàn bộ giữ trạng thái unvalidated.

## 1. Regression tests theo N4 mới

Đã rà soát 5 marker expectedFailure cũ:

- Bỏ marker cho parser false, thiếu giá, target dictionary âm và Cliff tách
  snapshot: các hành vi này đã được N4 sửa và nay là test thông thường.
- Thay test zero-shock chỉ so before/after bằng test so HF với phép tái dựng
  lượng token × giá × LT. Không để phép chép before sang after làm test đạt giả.
- Thêm test tiny-shock cho baseline đúng và baseline bất đồng; permission
  collateral E-Mode; NaN dictionary, NaN phần trăm và vô cực phần trăm.

Kết quả:

- **64 test N5 đạt**: 44 test_n5*.py + 20 test Cascade.
- Bộ N4 chính + contracts mới: **27 passed, 6 xfailed**.
- Riêng file contracts có 5 test đạt và 6 expected failures; không còn
  unexpected success do marker cũ. Sáu lỗi đã biết vẫn chưa được sửa ở N4:
  zero-shock bất đồng phép tái dựng, discontinuity tiny-shock, permission E-Mode,
  NaN dictionary, NaN phần trăm, infinity phần trăm.

Các expected failures không được coi là nghiệm thu. Khi N4 sửa đúng, unexpected
success yêu cầu xem lại rồi bỏ marker tương ứng.

Đã bổ sung kiểm thử N5 về nhận diện snapshot/block/hash/timezone, dữ liệu nhãn
B cũ không thay được B1, boundary cửa sổ sự kiện, graph/ledger và attribution
theo code N4 hiện hành. Các guard N5 không sửa hành vi API N4 gọi trực tiếp.

## 2. Bảng theo ví–token cho 60/58 ví bất đồng HF

Attribution đã chuyển từ mô hình membership đầu tiên của N4 cũ sang mô hình
N4 hiện tại: category-specific membership nhưng chưa xét is_collateral,
và collateral flag fallback theo reserve. Đã dùng cờ gốc của ví và lượng
collateral mà borrower_positions truyền vào N4 để kiểm phép tái dựng.

HF tái dựng **khớp tất cả 28.493/28.545 ví A/B** trong dung sai attribution.
Không còn residual chưa giải thích trong phép tái dựng hiện tại. Đây là kiểm
chứng số học nhánh code, không phải chứng minh nhân quả thị trường.

| Nguyên nhân trong nhóm khác phân loại HF=1 | A | B |
|---|---:|---:|
| Cờ collateral cấp ví khác fallback reserve | 59 ví; nợ 9.397.149,62 USD | 57 ví; nợ 9.398.261,16 USD |
| Membership trùng category nhưng permission collateral bị bỏ qua | 1 ví; nợ 5.372,71 USD | 1 ví; nợ 5.373,95 USD |
| Tổng | 60 ví | 58 ví |

Các giá trị là dư nợ của nhóm bất đồng, không phải bad debt hoặc khối lượng thanh lý.

File bàn giao N4 trong A/ và B/:

- `hf_disagreement_tokens.csv`: mọi token trong ví bất đồng; A có 122 dòng,
  B có 118 dòng. Có cờ collateral ví/adapter/N4, category được chọn, permission,
  LT chuẩn hóa, giá, lượng collateral N4 dùng, weighted collateral và đóng góp HF.
- `hf_attribution_disagreements.csv`: tổng theo ví, HF tái dựng, HF N4, residual,
  nhãn nguyên nhân và trạng thái numerically_reconstructed.
- `hf_cause_summary.csv`: tổng hợp nguyên nhân và dư nợ.
- `hf_attribution_by_token.parquet`, `hf_attribution_by_wallet.parquet`: đầy đủ
  toàn portfolio, không chỉ các ví đổi phân loại.
- `hf_unexplained_residuals.csv`: hiện rỗng, vẫn xuất schema để lần sau kiểm.

Attribution có hai mode tường minh trong n5_integration.py: category_membership
cho code hiện tại, first_membership chỉ dùng tái lập/bảo vệ test của bản N4 cũ.

Hướng sửa N4 và đường dẫn file dễ mở được ghi riêng ở
`docs/BAN_GIAO_N4_SAU_PULL_9b34ff7.md`. Chưa gửi tin nhắn ra ngoài dự án.

## 3. HF tái dựng và tiny-shock trên dữ liệu thật

`reconstructed_hf_validation.json` kiểm trực tiếp phép tính từ đầu vào N4,
khác với chỉ đối chiếu trước/sau zero-shock:

- 57.038 dòng ví–snapshot, không có evidence thiếu/nonfinite.
- 436 dòng HF cơ sở lệch HF tái dựng ≥0,005.
- 446 dòng có bước nhảy HF ≥0,005 từ shock 0 sang ETH shock −1e-12.
- 118 dòng khác phân loại HF cơ sở với HF tái dựng.
- Gate **failed**, dù zero_shock_validation.json chỉ so before/after đang passed.

Hai phép kiểm dùng nguồn khác nhau: attribution kiểm đúng nhánh HF N4, còn
reconstructed-HF kiểm nhánh Stress từ effective_lt và các cột vị thế. Vì vậy
số dòng sai số của chúng không bắt buộc bằng nhau.

File `reconstructed_hf_failures.csv` có số liệu theo ví để N4 đối chiếu.

## 4. QA A/B1/B2 và chọn cửa sổ thanh lý

Module mới `src/models/n5_snapshots.py` kiểm config ↔ metadata ↔ 8 bảng Parquet:
positions_long, borrower_positions, reserve_config, oracle_prices,
emode_membership, account_reference, protocol_reconciliation, hf_reconciliation.

Kết quả `snapshot_readiness.json`:

| Snapshot | Block cấu hình | Trạng thái |
|---|---:|---|
| A | 26179841 | passed_structure; chưa đồng nghĩa HF/research passed |
| B1 | 20458637 | missing_data: thiếu metadata và cả 8 bảng tại nhãn/block này |
| B2 | 23543615 | missing_data: thiếu metadata và cả 8 bảng tại nhãn/block này |

Nhãn B cũ được ghi stale_labels; graph/diagnostics B vẫn được giữ để xem dữ liệu
quan sát cũ, với snapshot_scope=legacy_metadata_only. Không đổi B thành B1/B2,
không dùng dữ liệu năm 2026 làm trạng thái lịch sử 2024/2025.

Cửa sổ sự kiện lấy tường minh từ config mới, yêu cầu đúng protocol,
event_block > snapshot_block, timestamp > timestamp snapshot và timestamp < end:

| Window | Sự kiện | Ví duy nhất | Ví trong giờ đầu |
|---|---:|---:|---:|
| B1: sau 2024-08-04 23:59:59 UTC, trước 2024-08-06 00:00:00 UTC | 1.830 | 1.152 | 177 |
| B2: sau 2025-10-09 23:59:59 UTC, trước 2025-10-12 00:00:00 UTC | 902 | 623 | 0 |

Đây là các sự kiện quan sát trong nguồn được bàn giao, chưa chứng nhận nguồn
đủ mọi sự kiện. Hit-rate giữ null vì chưa có prediction/state đúng snapshot.
Cửa sổ B2 trong config dài khoảng 48 giờ (B1 khoảng 24 giờ); không âm thầm cắt
về 24 giờ. Nhóm nghiên cứu cần chốt phạm vi này và thời điểm giờ đầu cho kiểm định.

File: `historical/configured_window_events.parquet`,
`historical/configured_window_qa.json`. Cột validation_snapshot_id là quan hệ
sự kiện với cửa sổ kiểm tra, không thay đổi nhãn snapshot của vị thế nguồn.

## 5. KPI, graph, Compound partial và đóng gói

- `kpi_mapping.csv` vẫn đối chiếu đủ 18 KPI; thêm `kpi_artifact_qa.json` kiểm
  tồn tại file/cột theo mapping: cả 18 passed_mapping_only. Không nâng các KPI
  not_implemented/definition_review/tracking_only thành đã hoàn thành.
- Graph 107.779 cạnh A, 107.993 cạnh B: bảo toàn collateral/debt exposure theo
  token và snapshot, không cộng trùng cạnh wallet_protocol. B là dữ liệu legacy.
- Adapter Compound vẫn giữ market, block, token, vai trò collateral/debt/base
  supply; không áp close factor Aave cho Comet.
- Ledger nhận delta tường minh; kiểm số dư ban đầu hữu hạn, giá dương, ID event
  duy nhất, không chi vượt số dư/không dùng vị thế unknown, không tạo infinity.
- Trên dữ liệu thật chỉ kiểm ledger rỗng giữ số dư: events_applied=0. Các delta
  có thay đổi chỉ thử bằng fixture. Không suy ra tỷ lệ rút hoặc lây lan chưa chốt.
- Coverage vẫn 99.782/114.076, 14.294 unknown_not_queried. Dữ liệu partial vẫn
  là unvalidated, không coi QA bảng đạt là coverage hoàn chỉnh.
- Manifest xác nhận byte của input, code và 69 output files; không phát hành
  manifest nghiệm thu hoặc tự ghi vào data/marts.

Đã sửa `scripts/n5_review_n4.py` để ghi nhận các đầu vào bị N4 từ chối thay vì
crash khi code mới chặn giá/target. Script đã được chạy thử thành công tại
`outputs/n5_preparation/n4_regression_postpull_9b34ff7/`.

## Tái lập

```powershell
python -B -X utf8 -m unittest discover -s tests -p "test_n5*.py" -v
python -B -X utf8 -m unittest discover -s tests -p test_cascade.py -v
python -B -X utf8 -m pytest tests/test_health_factor.py tests/test_stress.py tests/test_cliff_reverse.py tests/test_n4_pending_contracts.py -q -p no:cacheprovider
python -B -X utf8 -m scripts.n5_prepare_handoff --snapshot-config config/snapshots.json --output-dir outputs/n5_preparation/n5_postpull_NEW
```

Lệnh đóng gói cần thư mục mới. Không chạy crawler/RPC, không đọc .env, không cài
thư viện. Khi N2 bàn giao B1/B2, chạy lại để kiểm nhận diện; không tự sửa nhãn.
