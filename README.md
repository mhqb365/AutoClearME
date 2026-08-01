# Auto Clear ME

[English](#english) | [Tiếng Việt](#tiếng-việt)

---

## English

Auto Clear ME is a Windows utility for cleaning Intel CSME firmware from BIOS dumps, focused on ME 11 to ME 20.

The app uses ME Analyzer to analyze the selected BIOS dump, suggests compatible ME Region and Intel FIT versions, then tries to build a cleaned `_CLEARME` image. The original BIOS dump is never modified or overwritten.

### Features

- Supports Intel CSME 11 to 20.
- Automatically analyzes BIOS dumps with ME Analyzer.
- Suggests compatible ME Region and Intel FIT versions.
- Automatically retries remaining compatible FIT versions if one fails.
- Supports Single BIOS and Dual BIOS workflows.
- Saves cleaned BIOS files beside the original input files.
- Opens File Explorer after a successful clear.
- Supports English and Vietnamese UI text.
- Portable release with automatic dependency setup.
- Built-in GitHub Release update check.

### Requirements

- Windows 10 or later.
- Intel ME Analyzer, included in the release under `MEA\`.
- Intel ME Region repository, selected in `Settings`.
- Intel CSME System Tools / FIT, selected in `Settings`.

Intel ME Region files and Intel CSME System Tools are not included with this project. Users must provide their own copies.

### Download And Run

1. Open the GitHub `Releases` page.
2. Download the latest release ZIP file.
3. Extract the ZIP file to any folder.
4. Double-click `Run.bat`.
5. Wait while the launcher prepares the required components.
6. Auto Clear ME will open automatically.

`Run.bat` checks and installs only what is needed:

- Python 3, installed with `winget` if Python is not found.
- Latest `pip`.
- Python packages required by ME Analyzer:
  - `colorama`
  - `crccheck`
  - `pltable`

If `winget` is not available, install Python manually from <https://www.python.org/>, then run `Run.bat` again.

### Workflow

1. Open `Settings`.
2. Select `FIT root`.
3. Select `ME Region root`.
4. Select the UI language if needed.
5. Select a BIOS dump.
6. Wait for `Analyze success`.
7. Review or change the suggested `ME Region` and `FIT`.
8. Click `Clear ME`.

Single BIOS output:

```text
INPUT_CLEARME.bin
```

Dual BIOS output:

```text
FILE1_CLEARME.bin
FILE2_CLEARME.bin
```

Outputs are saved next to the selected input files. After a successful clear, File Explorer opens the output folder.

### Updates

When the app opens, it checks the latest GitHub Release.

If a newer version is available, the app asks before updating. If the user agrees, Auto Clear ME will:

1. Download the release ZIP from GitHub.
2. Extract it to a temporary folder.
3. Replace the portable app files.
4. Keep the local `config.json`.
5. Run `Run.bat` again to reopen the app.

### Build Portable Folder

For maintainers:

```text
Build.bat
```

The portable app is created in:

```text
dist\
```

The release ZIP is also created beside it:

```text
AutoClearME_VERSION.zip
```

Upload this ZIP file to GitHub Releases so the built-in updater can download the portable package.

Users can run:

```text
dist\Run.bat
```

`Build.bat` copies `config.example.json`, not the local `config.json`.

### Safety

- Keep an untouched backup of the original programmer dump.
- Use the correct ME Region SKU.
- Use a compatible Intel FIT version.
- Do not flash images that fail to build with FIT.
- Do not flash images if ME Analyzer reports unexpected firmware information.
- Perform the required platform reset, for example `fpt -greset`, after flashing when applicable.

### License

MIT. See `LICENSE`.

---

## Tiếng Việt

Auto Clear ME là công cụ Windows hỗ trợ làm sạch Intel CSME trong BIOS dump, tập trung cho ME 11 đến ME 20.

App dùng ME Analyzer để phân tích BIOS dump đã chọn, đề xuất ME Region và Intel FIT phù hợp, sau đó thử build file đã clear với hậu tố `_CLEARME`. File BIOS dump gốc không bao giờ bị chỉnh sửa hoặc ghi đè.

### Tính Năng

- Hỗ trợ Intel CSME 11 đến 20.
- Tự động phân tích BIOS dump bằng ME Analyzer.
- Đề xuất ME Region và Intel FIT phù hợp.
- Nếu một bản FIT lỗi, app tự thử các bản FIT phù hợp còn lại.
- Hỗ trợ Single BIOS và Dual BIOS.
- Lưu file BIOS đã clear ngay cạnh file input gốc.
- Mở File Explorer sau khi clear thành công.
- Hỗ trợ giao diện tiếng Anh và tiếng Việt.
- Bản portable có launcher tự chuẩn bị thư viện cần thiết.
- Có kiểm tra cập nhật qua GitHub Release.

### Yêu Cầu

- Windows 10 trở lên.
- Intel ME Analyzer, đã nằm trong release tại thư mục `MEA\`.
- Bộ Intel ME Region, chọn trong `Settings`.
- Intel CSME System Tools / FIT, chọn trong `Settings`.

Intel ME Region và Intel CSME System Tools không được phân phối kèm dự án này. Người dùng cần tự chuẩn bị.

### Tải Về Và Chạy App

1. Mở trang GitHub `Releases`.
2. Tải file ZIP của bản release mới nhất.
3. Giải nén file ZIP ra một thư mục bất kỳ.
4. Bấm đôi vào `Run.bat`.
5. Chờ launcher chuẩn bị các thành phần cần thiết.
6. Giao diện Auto Clear ME sẽ tự mở lên.

`Run.bat` sẽ kiểm tra và chỉ cài những thứ còn thiếu:

- Python 3, cài bằng `winget` nếu máy chưa có Python.
- Bản `pip` mới nhất.
- Các gói Python mà ME Analyzer cần:
  - `colorama`
  - `crccheck`
  - `pltable`

Nếu máy không có `winget`, hãy cài Python thủ công từ <https://www.python.org/>, sau đó chạy lại `Run.bat`.

### Cách Dùng

1. Mở `Settings`.
2. Chọn `FIT root`.
3. Chọn `ME Region root`.
4. Chọn ngôn ngữ giao diện nếu cần.
5. Chọn file BIOS dump.
6. Chờ đến khi hiện `Analyze success`.
7. Kiểm tra hoặc chọn lại `ME Region` và `FIT` được đề xuất.
8. Bấm `Clear ME`.

Kết quả Single BIOS:

```text
INPUT_CLEARME.bin
```

Kết quả Dual BIOS:

```text
FILE1_CLEARME.bin
FILE2_CLEARME.bin
```

File sau khi clear sẽ được lưu ngay trong thư mục của file input. Khi clear thành công, File Explorer sẽ tự mở thư mục chứa file kết quả.

### Cập Nhật

Khi mở app, app sẽ kiểm tra GitHub Release mới nhất.

Nếu có bản mới, app sẽ hỏi trước khi cập nhật. Nếu người dùng đồng ý, Auto Clear ME sẽ:

1. Tải file ZIP release từ GitHub.
2. Giải nén vào thư mục tạm.
3. Thay thế các file app portable.
4. Giữ lại `config.json` trên máy người dùng.
5. Chạy lại `Run.bat` để mở app lên lại.

### Đóng Gói Thư Mục Portable

Dành cho người bảo trì:

```text
Build.bat
```

App portable sẽ được tạo tại:

```text
dist\
```

File ZIP release cũng được tạo cùng cấp:

```text
AutoClearME_VERSION.zip
```

Upload file ZIP này lên GitHub Releases để updater trong app có thể tải đúng gói portable.

Người dùng có thể chạy:

```text
dist\Run.bat
```

`Build.bat` copy `config.example.json`, không copy `config.json` cá nhân.

### An Toàn

- Luôn giữ nguyên bản sao BIOS dump gốc.
- Chọn đúng SKU của ME Region.
- Dùng phiên bản Intel FIT phù hợp.
- Không flash nếu FIT báo lỗi khi build.
- Không flash nếu ME Analyzer báo thông tin firmware bất thường.
- Sau khi flash, chạy platform reset phù hợp, ví dụ `fpt -greset`, nếu quy trình sửa chữa yêu cầu.

### License

MIT. Xem `LICENSE`.
