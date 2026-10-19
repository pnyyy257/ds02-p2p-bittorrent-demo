# Thuyết trình bốn demo trực quan

Mở Terminal trong thư mục chứa `pyproject.toml`:

```powershell
python -m pip install -e ".[dev]"
python scripts/run_visual.py
```

Mở **http://127.0.0.1:8080**. Chọn Demo 1–4 hoặc chạy cả bốn. Dùng `python scripts/run_visual.py --port 8081` nếu cần cổng khác. Giữ Terminal chạy trong lúc trình bày; kết thúc bằng Ctrl+C. Dashboard dừng các tiến trình do nó tạo và giữ lại thư mục `runtime/visual-*/` chứa log, chunk và file tải về.

## Chọn tài liệu thật để demo

Bấm chọn file và chọn PDF, DOCX hoặc file bất kỳ từ máy, kích thước **8 byte–32 MiB**. File được tải lên ngay; chờ giao diện hiển thị đúng tên và kích thước trước khi chạy demo. Cả bốn demo dùng cùng file đã chọn nhưng có thư mục peer mới cho mỗi tình huống. Tên file và toàn bộ byte gốc được giữ nguyên; không chèn dữ liệu để làm file lớn hơn, không chuyển định dạng.

Sau khi demo hoàn tất và SHA-256 được xác minh, bấm tải file của B về máy. Mở file bằng ứng dụng PDF/Word như bình thường và có thể đối chiếu hash với bản gốc. Ở demo 2, C cũng có bản tải hoàn chỉnh.

Bấm **Dùng file mẫu** để dùng lại file tự tạo 4 MiB. File đã chọn được giữ cho các lượt chạy trong cùng phiên dashboard; khởi động lại dashboard sẽ quay về file mẫu. File dưới 8 byte không đủ chunk cho các tình huống chia sẻ/chuyển nguồn. Tên file chứa ký tự điều khiển, đường dẫn hoặc tên dành riêng của Windows bị từ chối.

## Giới thiệu mô hình

“Hệ thống gồm một tracker và ba peer A, B, C chạy thành bốn tiến trình độc lập. Tracker cung cấp metadata và danh sách nguồn. Dữ liệu file đi trực tiếp giữa các peer qua HTTP. Giao diện đang hiển thị trạng thái và sự kiện của các tiến trình này.”

Mỗi lượt demo có thư mục và cổng peer mới. Chỉ A được cấp file nguồn. Với file mẫu 4 MiB, A chia thành 64 chunk, mỗi chunk 64 KiB; với file được chọn, kích thước chunk tự điều chỉnh để có tối đa 64 chunk. B và C có kho dữ liệu riêng và chỉ nhận file qua mạng. Các ô chunk biểu diễn dữ liệu peer đang có; mũi tên xuất hiện theo sự kiện nhận chunk thành công. Các pha chuẩn bị cũng truyền dữ liệu thật và được ghi vào nhật ký.

## Demo 1 — B tải file từ A và C

Chọn Demo 1 và chạy. Chờ C tải bản sao từ A trong pha chuẩn bị, sau đó quan sát A → B và C → B khi B bắt đầu tải.

Lời trình bày: “B hỏi tracker các nguồn có chunk rồi mở các worker tải song song. Các chunk có thể đến từ A hoặc C. Tracker không chuyển tiếp nội dung file.”

Minh chứng: các sự kiện `DOWNLOAD_CHUNK` của B có nguồn A và C; mục kiểm chứng xác nhận B dùng cả hai nguồn. File cuối của B phải khớp kích thước và SHA-256 trong metadata.

## Demo 2 — B chưa tải xong đã chia sẻ chunk cho C

Chọn Demo 2 và chạy. B bắt đầu tải từ A với một worker. Khi B mới có một phần số chunk, C bắt đầu tải và có thể lấy ngay chunk đã xác minh từ B.

Lời trình bày: “B vừa là người tải, vừa là nguồn cung cấp. B không cần chờ có đủ file để chia sẻ một chunk hợp lệ cho C.”

Minh chứng: nhật ký có `UPLOAD` tại B với `completed=false`, đồng thời C ghi nhận B trong các nguồn đã sử dụng. Kiểm chứng cuối yêu cầu file của cả B và C khớp SHA-256. Chỉ nhìn thấy B → C sau khi B đã tải xong chưa đủ chứng minh tình huống này.

## Demo 3 — Phát hiện chunk hỏng và tải lại

Chọn Demo 3 và chạy. Sau khi C tải đủ bản sao, chương trình cố ý đổi một byte của chunk #1 tại C, giữ nguyên hash metadata. B tải từ A/C và kiểm tra từng chunk trước khi chấp nhận.

Lời trình bày: “B tính SHA-256 trên dữ liệu vừa nhận. Chunk #1 do C gửi có hash sai nên bị loại; B lấy lại đúng chunk đó từ A và tiếp tục tải.”

Minh chứng: sự kiện `INVALID_CHUNK` tại B có `source=peer-c`, `chunk=1`; tiếp theo có `DOWNLOAD_CHUNK` tại B cho `source=peer-a`, `chunk=1`. Hash toàn file cuối phải đúng. Đây là kiểm tra thay thế cùng một chunk bị hỏng, không chỉ kiểm tra bộ đếm lỗi.

Ô chunk hỏng của C giữ màu đỏ; ô tương ứng của B trở lại hợp lệ khi nhận bản thay thế từ A. B vẫn ghép được file đúng dù C còn giữ chunk hỏng.

## Demo 4 — A ngừng cung cấp dữ liệu, B chuyển sang C

Chọn Demo 4 và chạy. C có sẵn bản sao từ pha chuẩn bị. B bắt đầu tải từ A/C; chương trình chờ A đang phục vụ request rồi chuyển A sang ngừng phục vụ chunk.

Lời trình bày: “Trong tình huống này A vẫn chạy nhưng trả HTTP 503. B nhận lỗi, thử lại chunk từ C và hoàn tất file. Sau demo, A được bật phục vụ lại.”

Minh chứng: trạng thái A ngừng phục vụ, sự kiện `RETRY` của B và `DOWNLOAD_CHUNK` từ C cho chunk cần thử lại sau thời điểm gián đoạn; số request lỗi tăng và hash file cuối đúng.

Nếu cần minh họa dừng hẳn tiến trình, dùng kịch bản Docker/CLI riêng: `docker compose stop peer-a` khi B đang tải và C đã có bản sao; bật lại bằng `docker compose start peer-a`. Dashboard tự động dùng lỗi phục vụ HTTP 503.

## Lưu minh chứng và kiểm chứng lại

Với mỗi demo, lưu ảnh trạng thái chunk và sự kiện quan trọng, kết quả kiểm chứng, đường dẫn file đầu ra và SHA-256 hiển thị trên giao diện. Log gốc ở `runtime/visual-*/tracker.log`, `peer-a.log`, `peer-b.log`, `peer-c.log`. Dashboard giữ các sự kiện gần đây; dùng log khi cần đối chiếu đầy đủ hơn.

Với file tự chọn, lưu thêm ảnh file đầu ra mở bằng ứng dụng và kết quả hash trùng bản gốc. File nguồn được giữ trong `runtime/visual-inputs/` của controller rồi cấp riêng cho A; file tải xong nằm trong `runtime/visual-*/peer-b/downloads/` với tên gốc.

Có thể đối chiếu file B thủ công bằng PowerShell, thay đường dẫn bằng đường dẫn thực tế từ giao diện:

```powershell
Get-FileHash -Algorithm SHA256 "runtime\visual-...\peer-b\downloads\demo-1.bin"
```

Chạy lại cơ chế kiểm chứng của cả bốn demo mà không cần mở trình duyệt:

```powershell
python scripts/run_visual.py --check
# Dùng tài liệu riêng cho cả bốn tình huống:
python scripts/run_visual.py --check --file "C:\path\tai-lieu.pdf"
```

Lệnh này tạo các tiến trình mạng thật, kiểm tra bằng sự kiện và file đầu ra, in kết quả rồi thoát; mã thoát khác 0 thể hiện thất bại. Lưu kết quả chạy thực tế cùng minh chứng của máy trình bày.

## Kết quả chọn file ngày 19/10/2026

Trên Windows, Python 3.14.4: **47 bài kiểm thử đạt** và bốn demo terminal vẫn thành công. File `docs/input_specification.pdf` chạy đủ bốn demo với hash cuối khớp bản gốc. Trong Chrome, một DOCX hợp lệ có tên tiếng Việt, khoảng trắng và dấu `%` được chọn, giữ lại sau khi tải lại trang, truyền qua Demo 2, rồi tải về từ B với tên và nội dung giống hệt; bản của C cũng khớp. Kiểm tra file 8 byte qua đủ bốn tình huống và file 32 MiB qua Demo 1 đều đạt. Không có lỗi JavaScript hay tràn ngang ở chiều rộng 390px. Ảnh kiểm tra mới ở `runtime/visual-file-selected.png`, `visual-file-result.png`, `visual-file-mobile.png`.

## Kết quả nền trước khi thêm chức năng chọn file

Ngày 19/10/2026, trên Windows, Python 3.14.4, trước khi bổ sung chọn file thật và tải artifact: bộ test đạt **28/28**; `run_local.py` và `run_visual.py --check` đều hoàn tất bốn tình huống với file mẫu. Chrome chạy Demo 3 đạt 4/4 điều kiện, không ghi nhận lỗi JavaScript hay tràn ngang ở giao diện desktop và chiều rộng 390px. Demo 4 cũng được chạy lại qua dashboard sau khi siết kiểm chứng thứ tự lỗi A → tải lại cùng chunk từ C, kết quả đạt. Ảnh chụp kiểm tra nền nằm tại `runtime/visual-idle.png`, `visual-running.png`, `visual-result.png`, `visual-mobile.png`.
