# Auto Clear ME

![Auto Clear ME](AutoClearME.png)

## English

English | [Tiếng Việt](#tiếng-việt)

A tool to help clear ME BIOS 11+

The app uses ME Analyzer to analyze the selected BIOS dump, then suggests a matching ME Region and Flash Image Tool (FIT) version. The cleaned BIOS file is saved with the `_CLEARME` suffix, and the original BIOS dump is never modified or overwritten

### Features

- Supports ME BIOS 11+
- Automatically analyzes BIOS dumps with ME Analyzer
- Suggests matching ME Region and FIT versions
- Automatically tries the remaining compatible FIT versions if the first one fails
- Supports both Single BIOS and Dual BIOS workflows
- Saves the cleaned BIOS next to the original input file with the `_CLEARME` suffix
- Opens File Explorer after a successful clear
- Supports English and Vietnamese UI
- The release ZIP includes portable Python and required ME Analyzer dependencies
- Automatically checks for updates when a new release is available

ME Region and FIT are not distributed with this project. You must prepare them yourself, or download what I have here: [ME Region & FIT](https://drive.google.com/drive/folders/1ocp61oICeFGZuf-J59gpnLO88XGzKvPY?usp=sharing)

Download and extract them, then select the folders in `Settings`

### Download And Run

1. Open the [GitHub Releases](https://github.com/mhqb365/AutoClearME/releases)
2. Download the ZIP file from the latest release
3. Extract the ZIP file to any folder
4. Double-click `Run.bat`
5. The Auto Clear ME interface will open

The release ZIP already includes:

- Portable Python embeddable runtime
- Python packages required by ME Analyzer:
  - `colorama`
  - `crccheck`
  - `pltable`
- The bundled `MEA` folder
- `requirements.txt` as a fallback dependency list for manual Python setups

Users do not need to install Python, `pip`, or any Python package manually
If you choose to run with your own Python instead of the bundled runtime, install the same dependencies with `pip install -r requirements.txt`

### How To Use

1. Open `Settings`
2. Select the FIT folder in `FIT root`
3. Select the ME Region folder in `ME Region root`
4. Select a BIOS file
5. Wait until `Analyze success` appears
6. Review or change the suggested `ME Region` and `FIT`
7. Click `Clear ME`

Single BIOS output:

```text
INPUT_CLEARME.bin
```

Dual BIOS output:

```text
FILE1_CLEARME.bin
FILE2_CLEARME.bin
```

The cleaned file is saved in the same folder as the input file. After a successful clear, File Explorer opens the folder that contains the result

### Updates

When the app opens, it checks the latest GitHub Release

If a newer version is available, the app asks before updating. If the user agrees, Auto Clear ME will:

1. Download the release ZIP file from GitHub
2. Extract it to a temporary folder
3. Replace the portable app files
4. Keep the user's local `config.json`
5. Run `Run.bat` again to reopen the app

### Notes & Safety

- Always keep an untouched backup of the original BIOS dump
- Use a compatible FIT version
- If FIT fails, find the correct FIT version and place it inside the FIT folder
- Do not flash if FIT reports a build error
- Do not flash if ME Analyzer reports unexpected firmware information

### License

MIT

## Tiếng Việt

[English](#english) | Tiếng Việt

Công cụ hỗ trợ clear ME BIOS 11+

App dùng ME Analyzer để phân tích BIOS dump đã chọn, đề xuất ME Region và Flash Image Tool (FIT) phù hợp. BIOS đã clear có hậu tố `_CLEARME`, BIOS dump gốc không bị chỉnh sửa hoặc ghi đè

### Tính Năng

- Hỗ trợ ME BIOS 11+
- Tự động phân tích BIOS dump bằng ME Analyzer
- Đề xuất ME Region và FIT phù hợp
- Tự động thử các bản FIT phù hợp còn lại nếu bản đầu tiên thất bại
- Hỗ trợ Single BIOS và Dual BIOS
- Lưu file BIOS đã clear ngay cạnh file input gốc với hậu tố `_CLEARME`
- Mở File Explorer sau khi clear thành công
- Hỗ trợ giao diện tiếng Anh và tiếng Việt
- File ZIP release đã bao gồm Python portable và thư viện ME Analyzer cần thiết
- Tự động kiểm tra cập nhật khi có bản phát hành mới

ME Region và FIT không được phân phối kèm dự án này. Bạn tự chuẩn bị hoặc tải những gì tôi có ở đây: [ME Region & FIT](https://drive.google.com/drive/folders/1ocp61oICeFGZuf-J59gpnLO88XGzKvPY?usp=sharing)

Tải về và giải nén, sau đó chọn thư mục trong `Settings`

### Tải Về Và Chạy App

1. Mở trang [GitHub Releases](https://github.com/mhqb365/AutoClearME/releases)
2. Tải file ZIP của bản release mới nhất
3. Giải nén file ZIP ra một thư mục bất kỳ
4. Nhấp đúp vào `Run.bat`
5. Giao diện Auto Clear ME sẽ được mở lên

File ZIP release đã bao gồm sẵn:

- Python embeddable portable
- Các gói Python mà ME Analyzer cần:
  - `colorama`
  - `crccheck`
  - `pltable`
- Thư mục `MEA` đi kèm
- File `requirements.txt` để làm danh sách thư viện dự phòng khi chạy bằng Python tự cài

Người dùng không cần cài Python, `pip`, hoặc thư viện Python thủ công
Nếu muốn chạy bằng Python tự cài thay vì runtime đi kèm, cài thư viện bằng `pip install -r requirements.txt`

### Cách Dùng

1. Mở `Settings`
2. Chọn nơi chứa FIT trong ô `FIT root`
3. Chọn nơi chứa ME Region trong ô `ME Region root`
4. Chọn file BIOS
5. Chờ đến khi hiện `Analyze success`
6. Kiểm tra hoặc chọn lại `ME Region` và `FIT` được đề xuất
7. Bấm `Clear ME`

Kết quả Single BIOS:

```text
INPUT_CLEARME.bin
```

Kết quả Dual BIOS:

```text
FILE1_CLEARME.bin
FILE2_CLEARME.bin
```

File sau khi clear sẽ được lưu ngay trong thư mục của file input. Khi clear thành công, File Explorer sẽ tự mở thư mục chứa file kết quả

### Cập Nhật

Khi mở app, app sẽ kiểm tra GitHub Release mới nhất

Nếu có bản mới, app sẽ hỏi trước khi cập nhật. Nếu người dùng đồng ý, Auto Clear ME sẽ:

1. Tải file ZIP release từ GitHub
2. Giải nén vào thư mục tạm
3. Thay thế các file app portable
4. Giữ lại `config.json` trên máy người dùng
5. Chạy lại `Run.bat` để mở app lên lại

### Lưu Ý & An Toàn

- Luôn giữ nguyên bản sao BIOS dump gốc
- Dùng bản FIT phù hợp
- FIT lỗi thì tìm FIT đúng phiên bản bỏ vào folder chứa FIT
- Không flash nếu FIT báo lỗi khi build
- Không flash nếu ME Analyzer báo thông tin firmware bất thường

### License

MIT
