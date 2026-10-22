# N5 — Báo cáo kiểm định

## Cập nhật ngày 22/10/2026

Đã hoàn tất bộ chuẩn bị offline tại
`outputs/n5_preparation/offline_handoff_20261022_v2/`, gồm 35 file và manifest
SHA-256. Chi tiết: `docs/KET_QUA_N5_TU_THUC_HIEN_20261022.md`.

- Tổng nợ được N5 cộng độc lập và đối chiếu reserve đúng block A/B: đạt 1%;
  đây là kiểm tra debt-only, không thay thế gate collateral/TVL toàn giao thức.
- Graph Aave candidate: 107.779/107.993 cạnh A/B; kiểm bảo toàn exposure và schema.
- Đã xuất bảng HF từng ví/nhóm category, bảng số dư bụi và stress tách snapshot
  để điều tra. Không sửa code/marts N4 hoặc coi số liệu N4 hiện tại là đã nghiệm thu.
- DEX map được 3/65 token mỗi snapshot, còn 62 thiếu hướng; không dùng depth
  placeholder để chạy Cascade dữ liệu thật.
- Lịch sử 2024/2025: 3.068 candidate, 39 sự kiện bị loại vì thiếu giá; QA giá và
  concentration có báo cáo riêng. Không dùng bộ này thay holdout B 2026.
- Compound partial: position/account reconciliation đạt phép kiểm hiện có;
  vẫn thiếu 14.294 truy vấn. Giữ complete=false.
- Đã có demo rounds/summary, sensitivity, hai biểu đồ và manifest unvalidated.
- **46/46 test N5 đạt**; hash nguồn và file đầu ra khớp sau khi chạy.

Metadata N2 hiện đã có positions_exported=true, hf_validated=true; ghi chú về
hai cờ false ở báo cáo cũ bên dưới đã lỗi thời. Cờ metadata không thay thế kiểm
định độc lập. Các gate HF N4, số dư bụi, collateral/TVL, holdout và hiệu chỉnh
price impact vẫn chưa đủ để nghiệm thu nghiên cứu.

## Báo cáo lưu lại ngày 20/10/2026

**Trạng thái: chưa đủ điều kiện nghiệm thu kết quả nghiên cứu.** Các module
chuẩn bị và kiểm thử đã hoạt động; đây không phải chứng nhận dữ liệu toàn Aave,
kiểm định Cascade thực nghiệm hoặc bằng chứng lây lan sang Compound.

## Phạm vi đã thực hiện

- Adapter vị thế token N2 → Wallet/Asset và bảng chuẩn hóa dùng được cho Stress
  Test N4; nhận block/đường dẫn tường minh, không đổi cấu trúc dự án.
- Kiểm schema/khóa, kiểu dữ liệu, số dư, giá oracle, LT, bonus, E-Mode và HF.
- Kiểm tra tính sẵn sàng của bảng thanh lý holdout N3 tại Snapshot B.
- Khung sensitivity, benchmark tĩnh một vòng và kiểm tính nhất quán đầu ra.
- Kiểm thử trên fixture/8 ví giả lập; không chạy Cascade thực nghiệm trên toàn
  bộ dữ liệu thật khi gate chưa đạt.

Chỉ đọc các bảng Aave/N4/N3 được chỉ định. Không sửa code Compound, `.env`,
phân công, cấu hình market, Aave input, checkpoint hay file crawler đang ghi.
Không dừng tiến trình, không cài thư viện và không dùng/sửa `.venv` của crawler.
Chỉ bổ sung module N5, script, tài liệu, đầu ra riêng và đổi thư mục tạm của hai
test xuất Parquet N5 cũ để tránh lỗi quyền Windows TEMP. Giữ nguyên lõi Cascade
và thư mục mã nguồn N4.

## Đầu vào và kết quả

Nguồn: `data/processed/aave/{positions_long,reserve_config,oracle_prices,
emode_membership,account_reference}.parquet` và `snapshot_metadata.json`.
Marts N4 đọc từ `DeFiRisk-Lab-updated-main/data/marts/`.

| Kiểm tra | Snapshot A, block 26179841 | Snapshot B, block 26194188 |
|---|---:|---:|
| Ví có vị thế trong adapter | 28.493 | 28.545 |
| Tài sản trong vị thế | 65 | 65 |
| HF đối chiếu được với Aave | 28.493 | 28.545 |
| Median absolute error HF với Aave | 7,2675 × 10⁻¹² | 7,2782 × 10⁻¹² |
| Gate HF: median < 0,005 | Đạt | Đạt |
| Borrower có trong reference nhưng ngoài portfolio | 0 | 0 |
| Ví sai số tương đối nợ >1% | 220 | 229 |
| Ví sai số tương đối collateral >1% | 32 | 37 |
| Chênh lệch USD lớn nhất trong các khoản vượt 1% | ≈2,219 × 10⁻⁸ USD | ≈2,218 × 10⁻⁸ USD |
| HF N4 khác adapter tại ngưỡng thanh lý 1 | 369 ví | 368 ví |

Kiểm tra số dư theo ví dùng **ngưỡng tương đối nghiêm ngặt**, chưa cộng dung
sai USD tuyệt đối. Các trường hợp không đạt hiện thuộc khoản rất nhỏ; tỷ lệ
lớn không đồng nghĩa thất thoát có giá trị kinh tế lớn. Cần thống nhất với N1/N2
cách xử lý số dư bụi và độ phân giải USD on-chain trước khi nghiệm thu; chưa tự
nới ngưỡng để đổi trạng thái sang đạt. JSON ghi `null` cho sai số tương đối không
xác định khi reference bằng 0, kèm cờ `zero_reference` và chênh lệch USD chi tiết.

Reference còn có 58.793 (A) và 58.843 (B) tài khoản ngoài portfolio; kiểm tra
trong nhóm gọi thành công không tìm thấy tài khoản có dư nợ dương bị bỏ ngoài.
Điều này chỉ chứng minh độ khớp giữa hai bảng đang có, chưa chứng minh candidate
set bao phủ toàn bộ người vay trên giao thức.

## Vấn đề N4 cần đối chiếu trước khi tích hợp chính thức

Median HF N4 so với adapter bằng 0 ở cả hai snapshot, nhưng vẫn có 519 (A),
517 (B) ví có sai số tuyệt đối ≥0,005 và các trường hợp đổi phân loại tại HF=1.
Tổng dư nợ tính theo dữ liệu adapter của các ví đổi phân loại khoảng 1,277 tỷ
USD (A), 1,261 tỷ USD (B). Đây là **dư nợ của nhóm bất đồng**, không phải bad debt,
không phải lượng thanh lý và không chứng minh chúng đều sẽ bị thanh lý.

Ví dụ `0xcdfa7efe670869c6b6be4375654e0b206ef49c89`, E-Mode Category 3:

| Giá trị | Snapshot A | Snapshot B |
|---|---:|---:|
| HF Aave reported | 1,035624990647871 | 1,035591601208881 |
| HF adapter độc lập | 1,035624990647871 | 1,0355916012088808 |
| HF trong mart N4 | 0,8175986768272666 | 0,8175723167438532 |

Nhánh chọn E-Mode LT trong `DeFiRisk-Lab-updated-main/src/models/health_factor.py`
hiện kiểm Category 1. Cần N4 kiểm tra các category khác và nguồn dữ liệu tạo
mart; chưa kết luận mọi bất đồng có cùng nguyên nhân. Adapter N5 áp dụng bảng
membership theo category thực tế, đồng thời kiểm `lt_eff` N2 đã xuất. Không sửa
module hoặc tái tạo mart N4 trong đợt này.

Các bảng `borrower_risk`, `stress_positions`, `stress_summary`, `stress_heatmap`
có schema mẫu đạt bộ kiểm tra hiện tại. Chỉ kiểm tối đa 2.000 dòng đầu mỗi bảng
cho schema; **không nghiệm thu toàn bộ stress_positions hơn một triệu dòng**.
Heatmap ETH × depeg của N4 được nhận diện đúng, không bắt nhầm phải có BTC shock.

## Dữ liệu lịch sử N3 và các gate đang chờ

- `data/staging/n3_liquidations_holdout_24h.parquet`: 30 sự kiện, 30 ví duy nhất,
  không phát hiện trùng khóa sự kiện, đúng protocol và trong 24 giờ sau Snapshot B.
- Trong đó 29 ví có mặt ở portfolio B, 1 ví ngoài portfolio. Cần đối chiếu
  nguyên nhân (phạm vi mẫu, trạng thái ví tại snapshot, thay đổi vị thế sau đó).
- Không có sự kiện trong một giờ đầu sau snapshot trong bảng này. Chưa xác minh
  tính đầy đủ nguồn hoặc đây có phải cửa sổ sụt giảm phù hợp thiết kế nghiên cứu.
  Vì mẫu số giờ đầu bằng 0, không tính hay báo đạt hit-rate 85%.
- Bảng canonical `data/processed/fact_historical_liquidations.parquet` hiện có
  0 dòng tại thời điểm kiểm tra; staging có dữ liệu, chưa tự chép sang canonical.
- Chờ reference toàn reserve cùng block và phạm vi mẫu được thống nhất để kiểm
  gate sai lệch TVL/total debt ≤1%. Không lấy tổng từ chính mẫu làm chuẩn độc lập.
- Metadata vẫn khai báo `positions_exported=false`, `hf_validated=false` dù đã
  có file vị thế và đối chiếu HF sơ bộ; cần chủ dữ liệu xác nhận/cập nhật sau.
- Depth/price impact chưa hiệu chỉnh độc lập. Giá trị depth=1 triệu USD trong
  audit chỉ phục vụ dựng đối tượng; không chạy price impact bằng giá trị này.
- Chưa kiểm định kênh Compound, chưa đụng dữ liệu/crawler Compound đang hoạt động.

## Kiểm thử và đầu ra thử nghiệm

**39/39 kiểm thử N5 đã đạt** trong hai bộ: 20 test Cascade cũ và 19 test adapter,
validation, benchmark/sensitivity mới. Bao gồm chống trộn snapshot/protocol,
khóa trùng, dữ liệu thiếu/NaN, E-Mode, bảo toàn token, tổng repay, đường giá,
benchmark một vòng, lây lan vòng sau, shock kết hợp, giới hạn grid, cửa sổ lịch
sử, chống ghi đè và xuất Parquet. Chạy bằng Python hệ thống 3.12 với `-B`;
không chạy lại crawler hoặc toàn bộ test của các nhóm khác.

Demo giả lập: 64 lượt sensitivity và 4 so sánh benchmark, lưu tại
`outputs/n5_preparation/synthetic_demo_20261020/`. Không dùng làm kết quả nghiên cứu.

Báo cáo máy đọc cuối cùng:

- `outputs/n5_preparation/audit_A_final_20261020/audit.json`
- `outputs/n5_preparation/audit_B_final_20261020/audit.json`

Các thư mục audit có hậu tố phiên bản trước là kết quả chẩn đoán trung gian;
dùng hai báo cáo `final` ở trên cho lần bàn giao này. Trạng thái `failed` là kết
quả kiểm định đầu vào/đối chiếu, không phải test phần mềm thất bại. Chưa xuất
manifest nghiệm thu hoặc ghi vào marts chính thức.

## Việc tiếp theo sau khi cào Compound hoàn tất

1. N4 đối chiếu các ví đổi phân loại HF, đặc biệt E-Mode ngoài Category 1.
2. N1/N2 xác nhận mẫu, chuẩn TVL/debt độc lập, metadata và dung sai nợ bụi.
3. N1/N3 xác nhận Snapshot B, cửa sổ holdout, phạm vi sự kiện và chuỗi giá đủ tần suất.
4. Thống nhất cấu trúc import/đường dẫn; hiệu chỉnh tham số DEX, chạy sensitivity
   dữ liệu thật và kiểm định trước khi đóng gói `data/marts/manifest.json`.
