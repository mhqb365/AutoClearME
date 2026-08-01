## English

[Tiếng Việt](#tiếng-việt) | English

# Auto Clear ME

Auto Clear ME is a Windows utility for cleaning Intel CSME firmware (ME 11–20) from BIOS dumps.

The application uses ME Analyzer to analyze the selected BIOS dump, suggests compatible ME Region and Intel FIT versions, and automatically builds a cleaned `_CLEARME` image. If one compatible FIT version fails, the application automatically retries the remaining supported versions.

The original BIOS dump is **never** modified or overwritten.

---

## Features

* Supports Intel CSME 11–20.
* Automatically analyzes BIOS dumps with ME Analyzer.
* Suggests compatible ME Region and Intel FIT versions.
* Automatically retries compatible FIT versions if one fails.
* Supports both Single BIOS and Dual BIOS workflows.
* Saves the cleaned BIOS beside the original dump.
* Never overwrites the original BIOS dump.
* Portable release with automatic dependency setup.

---

### Requirements

* Windows 10 or later.
* Intel ME Analyzer (included in the release under `MEA\`).
* Intel ME Region repository.
* Intel CSME System Tools (FIT).

Configure the **ME Region Root** and **FIT Root** folders in **Settings** before using the application.

> **Note**
>
> Intel ME Region files and Intel CSME System Tools (FIT) are **not included** with this project. You must provide your own copies.

### Getting Started

1. Open the GitHub **Releases** page.
2. Download the latest release ZIP.
3. Extract the ZIP to any folder.
4. Double-click `Run.bat`.
5. Wait while the launcher prepares the required components.
6. Auto Clear ME starts automatically.

`Run.bat` installs only missing components:

* Python 3 (installed with `winget` if necessary)
* Latest `pip`
* Python packages required by ME Analyzer:

  * `colorama`
  * `crccheck`
  * `pltable`

If `winget` is unavailable, install Python manually from https://www.python.org/ and run `Run.bat` again.

### Workflow

1. Open **Settings**.
2. Configure **FIT Root**.
3. Configure **ME Region Root**.
4. (Optional) Select the interface language.
5. Select a BIOS dump.
6. Wait until analysis completes successfully.
7. Verify or change the suggested ME Region and FIT version.
8. Click **Clear ME**.

Single BIOS output:

```text
INPUT_CLEARME.bin
```

Dual BIOS output:

```text
FILE1_CLEARME.bin
FILE2_CLEARME.bin
```

The cleaned BIOS files are saved beside the selected input files.

After a successful operation, File Explorer automatically opens the output folder.

### Notes

* ME Analyzer starts automatically after a BIOS file is selected.
* In Dual BIOS mode, analysis starts after both BIOS files are selected.
* Temporary files are removed automatically.
* If one compatible FIT version fails, Auto Clear ME automatically retries the remaining compatible versions.
* The log window supports real-time updates.

### Safety

* Always keep an untouched backup of the original programmer dump.
* Use the correct ME Region SKU.
* Use a compatible Intel FIT version.
* Do not flash images that fail to build with FIT.
* Do not flash images if ME Analyzer reports unexpected firmware information.
* Perform the required platform reset (for example `fpt -greset`) after flashing when applicable.

### License

Licensed under the MIT License. See `LICENSE` for details.

---

## Tiếng Việt

Tiếng Việt | [English](#english)

# Auto Clear ME

Auto Clear ME là công cụ dành cho Windows giúp làm sạch (Clear ME) vùng Intel CSME (ME 11–20) trong BIOS dump.

Ứng dụng sử dụng ME Analyzer để phân tích BIOS dump đã chọn, tự động đề xuất ME Region và phiên bản Intel FIT phù hợp, sau đó tạo file BIOS đã được Clear ME với hậu tố `_CLEARME`. Nếu một phiên bản FIT tương thích không thể build, ứng dụng sẽ tự động thử các phiên bản tương thích còn lại.

File BIOS dump gốc **không bao giờ** bị chỉnh sửa hoặc ghi đè.

---

## Tính năng

* Hỗ trợ Intel CSME từ phiên bản 11 đến 20.
* Tự động phân tích BIOS dump bằng ME Analyzer.
* Tự động đề xuất ME Region và phiên bản Intel FIT phù hợp.
* Tự động thử các phiên bản Intel FIT tương thích khác nếu một phiên bản build thất bại.
* Hỗ trợ cả quy trình Single BIOS và Dual BIOS.
* Lưu file BIOS đã Clear ME ngay cùng thư mục với file gốc.
* Không bao giờ ghi đè lên file BIOS dump gốc.
* Phiên bản portable với khả năng tự động cài đặt các thành phần phụ thuộc.

---

### Yêu cầu

* Windows 10 trở lên.
* Intel ME Analyzer (đã có trong thư mục `MEA\` của bản phát hành).
* Bộ Intel ME Region.
* Intel CSME System Tools (FIT).

Trước khi sử dụng, hãy cấu hình **ME Region Root** và **FIT Root** trong **Settings**.

> **Lưu ý**
>
> Intel ME Region và Intel CSME System Tools (FIT) **không được phân phối** cùng dự án này. Người dùng cần tự chuẩn bị.

### Bắt đầu

1. Mở trang GitHub **Releases**.
2. Tải bản phát hành mới nhất.
3. Giải nén file ZIP vào một thư mục bất kỳ.
4. Chạy `Run.bat`.
5. Chờ launcher chuẩn bị các thành phần cần thiết.
6. Auto Clear ME sẽ tự động khởi động.

`Run.bat` chỉ cài đặt những thành phần còn thiếu:

* Python 3 (qua `winget` nếu chưa có)
* Phiên bản `pip` mới nhất
* Các thư viện Python mà ME Analyzer yêu cầu:

  * `colorama`
  * `crccheck`
  * `pltable`

Nếu máy không có `winget`, hãy cài Python thủ công từ https://www.python.org/, sau đó chạy lại `Run.bat`.

### Cách sử dụng

1. Mở **Settings**.
2. Chọn **FIT Root**.
3. Chọn **ME Region Root**.
4. (Tùy chọn) Chọn ngôn ngữ giao diện.
5. Chọn file BIOS dump.
6. Chờ quá trình phân tích hoàn tất.
7. Kiểm tra hoặc thay đổi ME Region và FIT được đề xuất.
8. Nhấn **Clear ME**.

Kết quả với Single BIOS:

```text
INPUT_CLEARME.bin
```

Kết quả với Dual BIOS:

```text
FILE1_CLEARME.bin
FILE2_CLEARME.bin
```

Các file BIOS sau khi Clear ME sẽ được lưu cùng thư mục với file gốc.

Sau khi hoàn tất, File Explorer sẽ tự động mở thư mục chứa kết quả.

### Ghi chú

* ME Analyzer tự động chạy ngay sau khi chọn file BIOS.
* Với Dual BIOS, quá trình phân tích bắt đầu sau khi chọn đủ hai file.
* Các file tạm được tự động xóa sau khi hoàn tất.
* Nếu một phiên bản FIT không build được, Auto Clear ME sẽ tự động thử các phiên bản tương thích còn lại.
* Vùng log hỗ trợ theo dõi quá trình xử lý.

### An toàn

* Luôn giữ nguyên bản sao BIOS dump gốc.
* Chọn đúng SKU của ME Region.
* Sử dụng phiên bản Intel FIT tương thích.
* Không flash nếu FIT báo lỗi khi build.
* Không flash nếu ME Analyzer phát hiện thông tin firmware bất thường.
* Sau khi flash, thực hiện platform reset (ví dụ `fpt -greset`) nếu quy trình sửa chữa yêu cầu.

### License

Phát hành theo giấy phép MIT. Xem `LICENSE` để biết thêm chi tiết.
