# DeFiRisk Lab
### Anatomy of a DeFi Crisis
**An On-Chain Risk Intelligence & Systemic Contagion Research Platform**

**Aave V3 · Compound III · Ethereum · Risk Analytics · Stress Testing · Liquidation Simulation**

[🌐 **Explore Interactive Dashboard**](https://defirisk-lab-45v6kgjkstvrxlwqniqt5l.streamlit.app/) | [📊 **Official Research Results**](outputs/n5_official_release_20261026/results/REPORT.md) | [📖 **Citation Pack**](outputs/n5_citation_pack_20261026/REPORT.md)

---

## 01. Project Overview — Tổng quan dự án

**What happens when one DeFi position falls?**

Một cú sốc giá tài sản không chỉ ảnh hưởng đến một vị thế vay. Khi Health Factor giảm xuống ngưỡng thanh lý, hoạt động xử lý tài sản thế chấp có thể tạo áp lực lên thanh khoản DEX, làm thay đổi giá thực thi và ảnh hưởng đến những vị thế khác.

**DeFiRisk Lab** được xây dựng để nghiên cứu cơ chế này bằng dữ liệu on-chain, phân tích rủi ro và mô phỏng định lượng.

Dự án tập trung vào hệ sinh thái **Aave V3 và Compound III trên Ethereum**, kết hợp dữ liệu vị thế vay, lịch sử thanh lý, giá thị trường, oracle, thanh khoản DEX và kết quả stress testing nhằm tìm hiểu:

- Rủi ro thanh lý tập trung ở những vị thế và nhóm tài sản nào?
- Cú sốc giá, depeg và cơ chế thanh lý tác động đến mức độ tổn thất ra sao?
- Hoạt động thanh lý có khả năng khuếch đại áp lực thị trường và hình thành rủi ro lây lan giữa các giao thức hay không?

Mục tiêu không chỉ là trực quan hóa các chỉ số, mà còn xây dựng một quy trình nghiên cứu **có thể kiểm tra nguồn dữ liệu, tái lập tính toán và phân biệt kết quả quan sát với kết quả mô phỏng**.

---

## 02. Research Framework — Khung nghiên cứu

| Research Question | Trọng tâm phân tích | Phương pháp |
|---|---|---|
| **Q1 — Borrower Risk** | Rủi ro vị thế vay, Health Factor, đòn bẩy và mức độ tập trung | On-chain reconstruction, risk metrics, statistical analysis |
| **Q2 — Market Stress** | Ảnh hưởng của ETH shock, depeg và thanh khoản đến tổn thất | Stress testing, Monte Carlo, scenario comparison |
| **Q3 — Systemic Contagion** | Thanh lý dây chuyền và khả năng truyền áp lực qua thị trường | Cascade simulation, network analysis, intervention experiments |

Nghiên cứu kiểm định sáu giả thuyết **H1a–H3b**. Mỗi giả thuyết có tiêu chí, dữ liệu đầu vào, kết quả và kết luận riêng.

Kết quả không ủng hộ giả thuyết vẫn được giữ nguyên; dự án không điều chỉnh số liệu để tạo ra kết luận mong muốn.

---

## 03. Research Highlights — Điểm nổi bật

| Hạng mục | Quy mô / đặc điểm |
|---|---|
| **Monte Carlo Simulation** | 120.000 kết quả kịch bản |
| **Counterfactual Experiments** | 1.458 cấu hình CF–shock–liquidity–feedback |
| **Historical Market Analysis** | Dữ liệu giá, depeg và thanh lý trong các giai đoạn nghiên cứu |
| **Cross-Protocol Analysis** | Aave V3 và Compound III |
| **Empirical Validation** | Đối chiếu trực tiếp và nội sinh, kiểm định giả thuyết |
| **Research Traceability** | Manifest, kiểm tra dữ liệu, phiên bản nguồn và tài liệu tái lập |

Các thí nghiệm bao gồm hiệu chỉnh dữ liệu trước sự kiện, phân tích cú sốc thị trường, so sánh kịch bản, kiểm tra độ nhạy và can thiệp nguồn/cạnh trong mô hình mạng.

**Lưu ý:** Quy mô mô phỏng không đồng nghĩa với bằng chứng nhân quả. Các kết quả phản ánh phạm vi dữ liệu, giả định và phương pháp nghiên cứu đã công bố.

---

## 04. Interactive Dashboard — Khám phá nghiên cứu

### [🚀 OPEN DEFIRISK LAB DASHBOARD](https://defirisk-lab-45v6kgjkstvrxlwqniqt5l.streamlit.app/)

Dashboard sử dụng **Streamlit, Python và Plotly** để trình bày các kết quả nghiên cứu dưới dạng tương tác.

Các không gian phân tích bao gồm:

| Module | Nội dung khám phá |
|---|---|
| **Overview** | Tổng quan mức độ phơi nhiễm và các kết quả rủi ro |
| **Historical Intelligence** | Lịch sử thị trường, sự kiện thanh lý và bằng chứng quan sát |
| **Borrower Risk** | Health Factor, tài sản thế chấp, dư nợ và phân bố rủi ro |
| **Stress Simulator** | Khám phá các kịch bản cú sốc và kết quả mô phỏng đã tính |
| **Liquidation Cascade** | Các vòng thực thi thanh lý, khoản nợ được xử lý và độ nhạy |
| **Methodology & Evidence** | Nguồn dữ liệu, phương pháp, giả định và giới hạn nghiên cứu |

Người xem có thể truy cập Dashboard trực tiếp bằng trình duyệt mà không cần cài đặt Python.

Dashboard là lớp trình bày kết quả, không thay thế bộ báo cáo và dữ liệu khoa học chính thức.

---

## 05. Data & Research Pipeline — Quy trình xử lý

Quy trình nghiên cứu được tổ chức theo các lớp:

**01 — Data Acquisition**

Thu thập dữ liệu on-chain, trạng thái vị thế vay, giao dịch thanh lý, oracle, giá tài sản và thanh khoản thị trường.

**02 — Data Processing & Validation**

Làm sạch, chuẩn hóa dữ liệu, đối chiếu snapshot, kiểm tra khóa sự kiện, độ bao phủ và khả năng tái lập.

**03 — Risk Reconstruction**

Phân tích vị thế vay, giá trị tài sản thế chấp, dư nợ, Health Factor và mức độ tập trung rủi ro.

**04 — Stress & Liquidation Modeling**

Áp dụng các cú sốc giá, cơ chế thanh lý và giả định thực thi để lượng hóa tác động.

**05 — Empirical Testing**

Kiểm định giả thuyết, so sánh kết quả, đánh giá độ nhạy và xác định giới hạn suy luận.

**06 — Visualization & Reporting**

Xuất dữ liệu nghiên cứu, báo cáo khoa học, bảng trích dẫn và Dashboard tương tác.

---

## 06. Technology Stack — Công nghệ sử dụng

| Nhóm công nghệ | Công cụ |
|---|---|
| Programming | Python |
| Data Processing | Pandas, NumPy, PyArrow |
| Statistical Analysis | SciPy, Statsmodels |
| Modeling | Monte Carlo, Stress Testing, Network & Cascade Models |
| Visualization | Streamlit, Plotly, Matplotlib |
| Blockchain Data | Ethereum on-chain data, RPC, event logs |
| Data Storage | Parquet, CSV, JSON |
| Collaboration & Deployment | Git, GitHub, Streamlit Community Cloud |

---

## 07. Repository Structure — Cấu trúc dự án

```text
DeFiRisk-Lab/
│
├── main.py                  # Streamlit Dashboard
├── dashboard_spaces.py      # Interactive research spaces
├── requirements.txt         # Python dependencies
│
├── config/                  # Research scenarios & configurations
├── src/
│   ├── data/                # Data acquisition & processing
│   ├── analysis/            # Descriptive & empirical analysis
│   └── models/              # Risk, stress & cascade models
│
├── scripts/                 # Research & validation workflows
├── data/                    # Raw, processed & analytical data
├── outputs/                 # Research artifacts & reports
├── tests/                   # Software & validation tests
├── docs/                    # Research documentation
└── README.md
```

Repository chứa cả dữ liệu lịch sử, các gói kiểm định và kết quả của nhiều giai đoạn phát triển. Không nên mặc định mọi thư mục `outputs/` đều thuộc bản kết luận chính thức.

---

## 08. Official Research Release — Kết quả chính thức

Bộ tài liệu chính thức được chốt ngày **26/10/2026**.

| Tài liệu | Vai trò |
|---|---|
| [Official Research Report](outputs/n5_official_release_20261026/results/REPORT.md) | Báo cáo và kết luận nghiên cứu Q1–Q3 |
| [Hypothesis Conclusions](outputs/n5_official_release_20261026/results/hypothesis_conclusions.csv) | Kết luận của sáu giả thuyết |
| [Publication Artifacts](outputs/n5_official_release_20261026/metadata/publication_artifacts.csv) | Danh sách dữ liệu và artifact dùng viết bài |
| [Citation Pack](outputs/n5_citation_pack_20261026/REPORT.md) | Bảng số liệu chuẩn để trích dẫn và đối chiếu |
| [Release Finalization Notes](docs/CHOT_BUNDLE_Q1_Q3_20261026.md) | Biên bản chốt bundle, KPI và manifest |

### Version Control & Scientific Integrity

Bản phát hành chính thức lưu các nguồn dữ liệu và mã liên quan đến quá trình tính toán để hỗ trợ kiểm tra, truy nguyên và tái lập.

Việc cập nhật code Dashboard hoặc workspace nghiên cứu không đồng nghĩa với việc sửa đổi bộ kết quả khoa học đã chốt.

Các nguồn hiện hành và quy tắc tham chiếu được mô tả tại:

`config/n5_research_artifacts.json`

**Khi trích dẫn kết quả nghiên cứu, ưu tiên Official Research Release và Citation Pack thay vì tự chọn một file trong các bản thử nghiệm cũ.**

---

## 09. Getting Started — Chạy dự án

### Clone repository

```bash
git clone https://github.com/TranLinh3906/DeFiRisk-Lab.git
cd DeFiRisk-Lab
```

### Tạo môi trường Python

```bash
python -m venv .venv
source .venv/bin/activate
```

Trên Windows, dùng lệnh kích hoạt phù hợp với PowerShell hoặc Command Prompt.

### Cài đặt thư viện

```bash
python -m pip install -r requirements.txt
```

### Chạy Dashboard

```bash
python -m streamlit run main.py
```

Sau khi khởi chạy, mở địa chỉ local do Streamlit cung cấp trong Terminal.

Dashboard đọc dữ liệu nghiên cứu đã xuất sẵn. Những tác vụ thu thập lại dữ liệu on-chain có thể yêu cầu cấu hình RPC, API hoặc điều kiện truy cập riêng và không thuộc bước chạy giao diện cơ bản.

---

## 10. Reproducibility — Khả năng tái lập

Dự án cung cấp cơ chế kiểm tra phiên bản và đối chiếu các artifact nghiên cứu.

Tài liệu tham khảo:

- [Hướng dẫn hoàn thiện kiểm định Q1–Q3](docs/HOAN_THIEN_KIEM_DINH_Q1_Q3_20261026.md)
- [Biên bản chốt P0.1–P0.5](docs/CHOT_BUNDLE_Q1_Q3_20261026.md)
- [Danh sách artifact chính thức](outputs/n5_official_release_20261026/metadata/publication_artifacts.csv)

Biên bản chốt nghiên cứu giải thích các vấn đề về đường dẫn lịch sử, lựa chọn bundle, định nghĩa KPI và kiểm tra manifest.

Bộ phát hành chính thức chứa bản sao các input và mã được sử dụng để giảm rủi ro kết quả thay đổi khi workspace tiếp tục phát triển.

---

## 11. Scientific Scope & Limitations

DeFiRisk Lab phân biệt ba loại kết quả:

**Observed Evidence:** Dữ liệu lịch sử hoặc trạng thái on-chain được thu thập và kiểm chứng trong phạm vi xác định.

**Modeled Outcomes:** Kết quả được tính từ mô hình rủi ro, stress testing và thực thi thanh lý theo các giả định đã khai báo.

**Conditional Research Conclusions:** Kết luận thống kê hoặc thực nghiệm chỉ có giá trị trong phạm vi dữ liệu, mẫu nghiên cứu và thiết kế kiểm định tương ứng.

Các kịch bản mô phỏng và quan hệ trong mô hình mạng không tự động chứng minh cơ chế lây lan nhân quả ngoài thị trường thực tế.

Dự án phục vụ nghiên cứu và giáo dục, **không phải công cụ khuyến nghị đầu tư hoặc bảo đảm dự báo thanh lý trong thời gian thực**.

---

## 12. Project Repository & Access

**GitHub:** https://github.com/TranLinh3906/DeFiRisk-Lab

**Interactive Dashboard:** https://defirisk-lab-45v6kgjkstvrxlwqniqt5l.streamlit.app/

**Official Results:** [View Research Report](outputs/n5_official_release_20261026/results/REPORT.md)

---

### DeFiRisk Lab — Understand the shock. Trace the liquidation. Investigate the contagion.

*From on-chain observations to reproducible risk intelligence.*
