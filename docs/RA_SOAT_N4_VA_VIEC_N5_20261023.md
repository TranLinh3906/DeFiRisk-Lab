# Rà soát N4 và việc N5 có thể tiếp tục — 23/10/2026

## Kết luận hiện tại

N4 đã sửa được lỗi merge `price_usd`; 12 test HF/Stress/Cliff hiện đều đạt.
Overview, stress_summary và heatmap đã tách snapshot. Tuy nhiên vẫn còn lỗi
E-Mode trong pipeline tạo HF, bất đồng zero-shock và gộp snapshot trong Cliff/
Reverse Stress. Không thể coi toàn bộ kết quả N4 đã qua kiểm định.

Không sửa code hay ghi đè marts N4 trong lần kiểm tra này. Đã tái chạy đúng
pipeline HF hiện tại nhưng chuyển thư mục ghi sang đầu ra review N5, sau đó
đối chiếu với adapter độc lập và mart hiện có.

Bằng chứng: `outputs/n5_preparation/n4_review_20261023/review.json`.
Script tái lập: `scripts/n5_review_n4.py`.

## Những phần đã sửa được từ lần kiểm tra trước

| Nội dung | Hiện tại |
|---|---|
| KeyError price_usd sau merge | Không còn tái hiện trong tests; 12/12 test N4 được chạy đều đạt |
| Giới hạn E-Mode Category 1 trong hàm HF | Hàm đã cho phép category khác 1 khi user/category config khớp, nhưng pipeline membership vẫn chưa đúng |
| stress_summary gộp A/B | Đã có snapshot_block, 38 dòng = 19 kịch bản × 2 snapshot |
| heatmap gộp A/B | Đã có snapshot_block, 60 dòng = 30 tổ hợp × 2 snapshot |
| overview gộp A/B | Code hiện trả dictionary snapshots riêng |

## Các phát hiện cần N4 xử lý

### 1. Ưu tiên cao: pipeline E-Mode làm mất membership theo category

Trong `src/models/health_factor.py` phần `main`, membership được loại trùng
theo `(snapshot_block, protocol_id, token_address)` rồi giữ dòng đầu. Một token
có thể nằm trong nhiều category, nên khóa này làm mất các membership hợp lệ.
Hiện có 58 khóa block–token thuộc nhiều category. Phần ghép cũng không đưa cờ
`is_collateral` của membership vào quyết định áp dụng E-Mode LT.

Cần ghép theo category người dùng và membership tương ứng từng token tại block;
kiểm tra `is_collateral`, không giữ một category đại diện cho toàn token.
Chỉ mở rộng điều kiện từ category 1 sang category >0 chưa giải quyết đủ lỗi này.

Đối chiếu dữ liệu hiện tại:

| Chỉ tiêu | Snapshot A | Snapshot B |
|---|---:|---:|
| Ví được đối chiếu | 28.493 | 28.545 |
| Ví khác phân loại tại HF=1 | 191 | 190 |
| Ví sai số HF ≥0,005 | 297 | 294 |
| Dư nợ nhóm bất đồng phân loại | 329,18 triệu USD | 326,41 triệu USD |

Đây là dư nợ nhóm bất đồng, không phải bad debt hoặc khối lượng thanh lý.
Chạy lại pipeline hiện tại vào thư mục riêng cho cùng số lượng sai lệch với
mart đang dùng: không thể giải quyết bằng việc chỉ chạy lại code chưa sửa.

Ví dụ A, ví `0xda43cce1263c1b8c8bfacf517fc7dd19d7dd41bd`, category 10:
HF adapter khoảng 1,025041, HF N4 khoảng 0,826646, dư nợ khoảng 54,74 triệu USD.
Đây là bằng chứng bất đồng cần điều tra, không chứng minh mọi ví có cùng nguyên nhân.

File: `A_current_mart_disagreements.csv`, `B_current_mart_disagreements.csv` và
các bảng Parquet comparison trong thư mục review.

### 2. Ưu tiên cao: zero-shock chưa giữ nguyên HF cơ sở

Chạy `run_stress_test` với không có cú sốc trên 57.038 dòng ví–snapshot:

- 590 dòng có |hf_before − hf_after| ≥0,005.
- 381 dòng đổi phân loại tại HF=1, dù không có cú sốc.

Code lấy hf_before từ mart N4 nhưng hf_after tính lại từ effective_lt trong
vị thế. Cần thống nhất công thức và tham số đầu vào để hai nhánh khớp nhau;
không sửa bằng cách chép hf_before sang hf_after nhằm che sai lệch.

File: `zero_shock_hf_mismatch.csv`.

### 3. Ưu tiên cao: Cliff/Reverse Stress vẫn gộp snapshot

`cliff_reverse.py` vẫn nhận cả A/B, cộng dư nợ rồi đếm ví duy nhất. Probe hai
bước 0% và −1% cho baseline debt khoảng 18,764 tỷ USD, bằng tổng hai thời điểm.
Đầu ra không có snapshot_block. Mart cliff hiện tại vẫn 61 dòng không có khóa
snapshot. `run_reverse_stress` cũng ghi rõ dùng tổng baseline qua các snapshot.

Cần chọn đúng một snapshot hoặc group riêng và giữ khóa snapshot trong đầu ra.
Test hiện tại chủ yếu dùng một snapshot nên chưa bắt được trường hợp này.

File probe: `mixed_snapshot_cliff_probe.csv`.

### 4. API đầu vào còn các trường hợp có thể tạo kết quả sai mà không báo lỗi

Các tình huống dưới đây đã tái hiện bằng dữ liệu nhỏ; chưa kết luận dữ liệu thật
hiện có đang chứa các giá trị lỗi đó:

- Stress dùng `astype(bool)` nên chuỗi `"false"` bị hiểu là True; probe vẫn tính
  collateral và cho HF=1,6 thay vì 0.
- Thiếu giá, không có USD fallback: code điền giá 0, khiến nợ 50 token biến thành
  0 USD và HF=999. Cần từ chối đầu vào thiếu thay vì coi thành tài khoản không nợ.
- Hàm HF giữ giá nhúng đang có dù bảng giá oracle truyền riêng khác. Probe có
  100 token, giá nhúng 1, oracle 2: kết quả collateral 100 USD. Cần chốt nguồn
  ưu tiên và kiểm sai lệch; test mới hiện cố ý xác nhận hành vi giữ giá nhúng.
- Reverse Stress nhận `{'usd': -1}` và `'-10%'` mà không báo lỗi, vì các nhánh
  dictionary/percent chưa kiểm hữu hạn và không âm như nhánh số thông thường.

### 5. Quy tắc shock cần thống nhất giữa N4/N5

N4 đang áp stETH depeg cho cả WEETH/CBETH, trong khi N5 mặc định chỉ áp phần
depeg thêm cho STETH/WSTETH. Cùng depeg −5%, N4 trả multiplier WEETH=0,95;
N5 mặc định giữ 1 nếu không có ETH shock. Đây là khác giả định mô hình, cần chốt
phạm vi cú sốc; không tự kết luận mọi LST phải hoặc không phải chịu cùng depeg.

## Việc N5 có thể làm thêm ngay

1. **Cập nhật validator khóa nhiều snapshot.** `n5_validation.py` vẫn dùng
   scenario_id làm khóa stress_summary, và chỉ dùng shock làm khóa heatmap.
   Vì thế bảng N4 mới hợp lệ theo snapshot bị báo 19/30 lỗi trùng giả. Cần thêm
   snapshot vào khóa khi có cột này, giữ tương thích bộ một snapshot cũ và bổ
   sung test chống nhầm khóa.
2. **Bổ sung regression tests và gate độc lập:** zero-shock nhiều snapshot,
   token thuộc nhiều category, is_collateral=False, thiếu/conflict giá, chuỗi
   false, target âm/NaN. N5 có thể chuẩn bị bằng chứng/test trước khi N4 sửa.
3. **Cập nhật bộ QA theo nguồn hiện tại**, sau khi validator tương thích schema
   mới. Không dùng kết quả 369/368 của bundle 22/10 để mô tả mart hôm nay.
   Hash các nguồn borrower_risk/stress_positions đã đổi so với manifest bundle cũ.
4. **Phân tích sâu sai lệch HF và zero-shock**, xuất danh sách/category/số dư
   để bàn giao N4. Phần bằng chứng cơ bản đã thực hiện trong lần review này.
5. **Bổ sung mapping KPI và bảo toàn graph**, đối soát các tổng theo block;
   chuẩn bị pipeline phân nhóm Q1 và thống kê Q3 trên graph quan sát, ghi rõ
   đâu là cấu trúc mạng và đâu là tác động lây lan chưa được mô phỏng.
6. **Chuẩn bị adapter/tracking Compound partial và cấu hình shock tường minh**
   ở phía N5, không công bố kết quả cho phần chưa phủ; yêu cầu mọi tham số truyền
   dẫn chưa thống nhất phải được cung cấp rõ thay vì tự điền.

Vẫn chưa đủ điều kiện phát hành Cascade/sensitivity thực nghiệm đã nghiệm thu:
HF/zero-shock chưa đạt, depth chưa phủ đủ tài sản và chưa hiệu chỉnh đầy đủ,
gate collateral/TVL và holdout B còn chờ. Compound vẫn thiếu 14.294 truy vấn
trong bộ merge partial hiện có. Không có bằng chứng mới để thay các trạng thái
chờ này bằng passed.

## Tái lập

```powershell
python -B -X utf8 -m pytest tests/test_health_factor.py tests/test_stress.py tests/test_cliff_reverse.py -q -p no:cacheprovider --tb=short
python -B -X utf8 -m scripts.n5_review_n4 --output-dir outputs/n5_preparation/n4_review_NEW
```

Mỗi lần review cần tên thư mục mới. Không chạy các lệnh main N4 trực tiếp nếu
chỉ muốn kiểm tra vì chúng ghi vào marts; script review đã chuyển nơi ghi HF
sang thư mục N5 riêng và chỉ gọi Stress/Cliff trong bộ nhớ.

Bản code trong `DeFiRisk-Lab-updated-main/` hiện khác HF/Stress ở thư mục gốc;
Cliff giống nhau. Cần dùng đúng bản gốc khi đối chiếu và tránh import nhầm bản cũ.
