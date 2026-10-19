# BÁO CÁO KỸ THUẬT (BẢN NHÁP CÓ THỂ CHỈNH SỬA)

**Môn học:** Distributed Systems  
**Đề tài:** DS02 — P2P File-Sharing System Simulating BitTorrent  
**Nhóm:** [Điền tên nhóm, họ tên, MSSV, lớp, giảng viên]  
**Mã nguồn:** [Điền liên kết GitHub sau khi nhóm tạo repository]  
**Phiên bản phần mềm:** 1.0.0  
**Ngày kiểm thử:** 17/10/2026  

*Đây là bản báo cáo nền tảng kèm sản phẩm chạy được. Nhóm cần bổ sung ảnh chụp minh chứng, thông tin thành viên, nguồn tham khảo nếu thêm thư viện/thuật toán khác và số liệu thực nghiệm trên máy nhóm trước khi nộp.*

## 1. Tóm tắt đề tài

Đồ án xây dựng hệ thống mô phỏng chia sẻ file ngang hàng (peer-to-peer) theo các nguyên lý cốt lõi của BitTorrent. Một file gốc được phân chia thành nhiều chunk có kích thước cố định; mỗi chunk có mã băm SHA-256 độc lập, đồng thời toàn bộ file cũng có mã băm SHA-256. Các peer khám phá nhau qua một tracker trung tâm, nhưng dữ liệu file không đi qua tracker. Sau khi nhận một chunk hợp lệ, một peer có thể đồng thời cung cấp chunk đó cho các peer khác, kể cả khi chưa tải hoàn tất file.

Hệ thống hiện thực bằng Python, FastAPI, aiohttp, asyncio và Docker Compose. Mô hình triển khai có bốn tiến trình độc lập: tracker và ba peer A, B, C. Các yêu cầu cốt lõi được triển khai gồm đăng ký peer, công bố metadata, tra cứu nguồn, tải song song nhiều chunk, kiểm tra hash, ghép file, khôi phục khi peer mất kết nối và tiếp tục tải từ dữ liệu đã lưu.

Bộ mã nguồn đã được kiểm chứng bằng 20 test tự động thành công và một chuỗi bốn kịch bản demo chạy qua HTTP giữa các tiến trình độc lập: tải hai nguồn, chia sẻ khi chưa tải xong, phát hiện chunk hỏng và chuyển sang nguồn dự phòng. Kết quả này là kiểm chứng cục bộ, không đại diện cho đánh giá hiệu năng hoặc kiểm thử WAN ở quy mô lớn.

## 2. Mô tả vấn đề và phạm vi

Trong mô hình client-server truyền thống, một máy chủ duy nhất phục vụ tất cả người tải. Khi số người tải tăng, băng thông và tải của máy chủ trở thành nút nghẽn; máy chủ ngừng hoạt động có thể làm gián đoạn toàn bộ phiên tải. Trong kiến trúc P2P, các máy đang sở hữu dữ liệu trở thành nguồn cung cấp cho máy khác. Phần đã tải được có thể tái sử dụng ngay, nhờ đó chia sẻ tải lên giữa nhiều peer.

Đề DS02 yêu cầu mô phỏng nguyên lý BitTorrent chứ không bắt buộc triển khai giao thức BitTorrent thực, magnet link, distributed hash table hoặc NAT traversal. Phạm vi sản phẩm là mô hình chạy được với một tracker, tối thiểu ba peer và cơ chế phân chia chunk/tải từ nhiều nguồn. Tracker chỉ cung cấp metadata và danh sách chunk mà peer sở hữu, không lưu hoặc chuyển tiếp nội dung file.

Phạm vi an toàn hiện tại là mạng nội bộ hoặc mạng Docker được kiểm soát; hệ thống dùng các token chung cho việc điều khiển và vận chuyển dữ liệu trong môi trường demo. Các giới hạn này được trình bày rõ để tránh nhầm lẫn với hệ thống chia sẻ file triển khai công khai trên Internet.

## 3. Phân tích yêu cầu chức năng

Yêu cầu về metadata: lưu định danh file, tên, kích thước, kích thước chunk, số chunk, danh sách hash từng chunk, hash toàn file và địa chỉ tracker. Seeder A tạo metadata bằng cách đọc file gốc thành từng phần, đồng thời lưu các phần dữ liệu để phục vụ yêu cầu tải đến từ mạng.

Yêu cầu về chia sẻ và khám phá: mỗi peer có Peer ID duy nhất; tracker ghi nhận địa chỉ peer, file ID, danh sách chunk hiện có và thời điểm công bố gần nhất. Các peer lần lượt gửi thông báo khi sở hữu chunk mới. Peer có thể tìm các nguồn đang có chunk qua tracker trước khi tải.

Yêu cầu về truyền dữ liệu: mỗi peer có HTTP upload endpoint và async downloader. Downloader phải lấy dữ liệu đồng thời từ ít nhất hai nguồn trong kịch bản có hai seeder; số worker được giới hạn để tránh tạo quá nhiều kết nối. Một peer tải dở vẫn có thể upload các chunk đã xác minh.

Yêu cầu về tính đúng: kiểm tra kích thước và SHA-256 của từng chunk; loại bỏ dữ liệu sai; ghép chunks theo thứ tự rồi xác minh toàn bộ file. Khi nguồn mất kết nối hoặc trả dữ liệu lỗi, downloader cập nhật danh sách nguồn và thử peer khác. Chunk hợp lệ đã tải được duy trì trên đĩa để phục hồi sau khi tiến trình khởi động lại.

## 4. Yêu cầu phi chức năng và tiêu chí nghiệm thu

Hệ thống phải có ít nhất ba tiến trình hoặc nút độc lập, mỗi nút có định danh riêng và trao đổi thông qua mạng, không sử dụng vùng nhớ chung để thay cho thông điệp. Các thành phần phải xử lý nhiều thao tác đồng thời, thể hiện được tính nhất quán metadata và chunk, cơ chế timeout/retry và ít nhất hai tình huống lỗi có phục hồi. Log phải hiển thị nút, thời gian, loại sự kiện và kết quả xử lý.

Yêu cầu bảo mật cơ bản bao gồm xác thực yêu cầu điều khiển và trao đổi chunk, không đưa bí mật thực vào kho Git, kiểm tra dữ liệu đầu vào và giới hạn kích thước dữ liệu. Ở mức thử nghiệm, hệ thống đặt ngưỡng file 1 GiB, chunk tối đa 4 MiB, số worker tải tối đa 16; thông tin xác thực được đọc từ biến môi trường.

Tiêu chí kiểm thử từ đề môn học gồm tối thiểu tám bài chức năng, hai bài concurrency/conflict, hai bài fault/recovery, hai bài security/invalid data và một thí nghiệm performance/scalability. Bộ test đi kèm đáp ứng số lượng các nhóm kịch bản này; khi nộp cần lưu log hoặc ảnh chụp máy nhóm để minh chứng chạy lại thành công.

## 5. Kiến trúc tổng quan

Mô hình gồm tracker, Peer A, Peer B và Peer C. Tracker giữ bảng file metadata và danh sách peer sở hữu từng chunk. Peer A được cung cấp file gốc ban đầu, tạo chunk và công bố lên tracker. Peer B và Peer C dùng tracker để tìm nguồn; sau đó gửi HTTP GET trực tiếp đến API chunk trên một peer được chọn. Tracker không có API trả nội dung file và không được mount thư mục dữ liệu chia sẻ.

Mỗi peer là một ứng dụng độc lập chạy HTTP server để cung cấp dữ liệu và HTTP client để nhận dữ liệu từ peer khác. Vai trò seeder/leecher không cố định: một peer đang download có thể đồng thời upload phần đã nhận. Docker Compose giúp chạy và kiểm soát từng tiến trình riêng biệt, với thư mục lưu trữ và danh tính của từng nút độc lập.

Trong bản mẫu, tên host Docker `tracker`, `peer-a`, `peer-b`, `peer-c` được dùng cho kết nối nội bộ. Các port host tương ứng là 8000, 9001, 9002 và 9003, chỉ nhằm thuận tiện khi demo và điều khiển chương trình. Dữ liệu chunk được chuyển trực tiếp qua các port 9000 nội bộ của các container peer.

## 6. Thiết kế các thành phần

Module `common.py` định nghĩa schema metadata, hàm SHA-256, kiểm tra độ dài chunk, chuẩn hóa tên file và ghi file nguyên tử. Module `tracker.py` tổ chức các API đăng ký/thông báo file và tra cứu các peer. Dữ liệu tracker được lưu trong RAM và được bảo vệ khỏi cập nhật đồng thời bằng asyncio Lock.

Module `peer.py` quản lý danh mục chunk cục bộ, lưu checkpoint, tạo file seed, nhận yêu cầu upload và điều phối worker tải. Mỗi chunk có đường dẫn dựa trên file ID và chỉ số thứ tự. Các chunk được ghi vào file tạm trước khi thay thế bằng os.replace để người đọc không thấy chunk viết dở. Module `cli.py` là công cụ dòng lệnh để thao tác seed, download và xem trạng thái.

Scripts demo tạo file kiểm thử riêng cho Seeder A, kích hoạt các tình huống phân tán và xác nhận hành vi bằng trạng thái API. Scripts performance đo tốc độ truyền theo các mức concurrency. Docker Compose định nghĩa bốn dịch vụ, network và volume cần thiết. Bộ tests tổ chức unit test và integration test trên tiến trình thực.

## 7. Định danh và cơ chế khám phá

Mỗi peer sở hữu một Peer ID dạng chuỗi, ví dụ `peer-a`. Định danh này được kiểm tra đầu vào và được dùng làm khóa trong bảng availability của tracker. Mỗi file được gắn File ID bằng chính mã SHA-256 của toàn bộ nội dung file; lựa chọn này cho phép dùng checksum chung trong kiểm tra danh tính nội dung khi chạy bản thử nghiệm.

Khi Seeder đăng ký, tracker ghi lại metadata và tập chunk đang sở hữu. Định kỳ khoảng ba giây, peer công bố lại danh sách chunk của các file cục bộ. Tracker lưu thời điểm nhận thông báo dưới dạng thời gian monotonic. Bản ghi peer chỉ được đưa vào danh sách nguồn trực tuyến khi chưa vượt quá giới hạn lease, mặc định 12 giây.

Mỗi lần chọn nguồn để tải, downloader truy vấn tracker lấy bản snapshot metadata và availability hiện tại. Cơ chế này đơn giản, dễ giải thích và hỗ trợ nhận biết những chunk vừa được peer khác tải thành công. Đổi lại, tracker nhận nhiều truy vấn nếu số chunk tăng; đây là điểm cần tối ưu trong phiên bản mở rộng.

## 8. Giao thức và định dạng thông điệp

Các thông điệp điều khiển sử dụng HTTP và JSON. Tracker hỗ trợ GET danh sách file, GET metadata kèm danh sách nguồn, PUT announce và DELETE peer khi rời mạng có trật tự. Dịch vụ Peer hỗ trợ POST seed, POST start-download, GET tiến trình download, GET chunk và các endpoint debug để mô phỏng sự cố.

Thông điệp `announce` chứa Peer ID, Peer URL, metadata đầy đủ và danh sách chỉ số chunk. Tracker từ chối trường hợp danh sách chunk trùng số, chỉ số ngoài phạm vi hoặc metadata xung đột với file ID đang tồn tại. Thông điệp tra cứu trả metadata và danh sách peer online gồm Peer ID, Peer URL và tập chunk.

Khối dữ liệu file chỉ đi qua `GET /api/chunks/{file_id}/{index}`, trả binary dạng application/octet-stream trực tiếp giữa hai peer. Kích thước phản hồi bị kiểm soát theo kích thước chunk dự kiến; downloader dùng token trao đổi riêng. Các HTTP status và log phân biệt kết nối không thành công, nguồn không có chunk, lỗi hash và tác vụ tải thành công.

## 9. Metadata, chunking và checksum

Seeder đọc file nguồn trong thư mục shared riêng của mình. Ở mức mặc định, mỗi chunk chứa 65.536 byte; chunk cuối có thể nhỏ hơn. Mỗi lần đọc, chương trình tạo SHA-256 cho chunk; song song đó là mã băm toàn file để tạo định danh file và kiểm tra tính đúng đắn của metadata.

Đối với file có kích thước S và chunk size C, số chunk N = ceil(S/C). Nếu file rỗng thì N = 0. Metadata bảo đảm số hash đúng bằng N và file ID trùng hash toàn file. Trước khi lưu chunk nhận qua mạng, downloader kiểm tra đồng thời độ dài byte và SHA-256 có khớp với metadata mong đợi không.

Sau khi nhận đủ các chunk đã xác minh, chương trình ghép lần lượt index từ 0 đến N-1 vào file tạm. Hash được tính lại trong lúc ghép. Chỉ khi độ dài cuối và hash toàn file đúng, chương trình mới đổi tên nguyên tử để công bố file hoàn chỉnh trong thư mục downloads.

## 10. Thuật toán tải song song

Khi người dùng tạo tác vụ tải, peer gọi tracker để lấy metadata và danh sách nguồn, sau đó tạo một hàng đợi gồm các chỉ số chunk còn thiếu. Số worker tải không vượt quá ngưỡng cấu hình; mỗi worker lấy một chỉ số riêng từ queue và tìm danh sách peer sở hữu chunk đó.

Để phân phối tải, danh sách nguồn được sắp xếp theo Peer ID rồi quay vòng dựa trên chỉ số chunk. Worker ưu tiên peer chưa thử cho chunk hiện tại; khi một peer timeout, trả HTTP lỗi hoặc chuyển chunk có hash sai, chương trình ghi log và chọn lại nguồn cho lần thử kế tiếp. Số lượt thử được giới hạn nhằm tránh vòng lặp vô hạn.

Khi một chunk đạt kiểm tra, peer ghi vào đĩa, cập nhật checkpoint và gửi thông báo tới tracker ngay lập tức. Nhờ vậy, một peer thứ ba có thể khám phá và tải từ peer này trong lúc tác vụ download ban đầu vẫn đang chạy. Đây là hành vi cốt lõi mô phỏng cơ chế swarm của BitTorrent.

## 11. Điều khiển đồng thời

Hệ thống có nhiều luồng logic bất đồng bộ: HTTP server đang trả chunk, worker downloader đang lấy chunk khác và heartbeat đang thông báo availability lên tracker. Những thành phần này chạy đồng thời trong một peer mà không cần dùng shared-memory giữa các tiến trình. Các peer chỉ nhận biết trạng thái của nhau thông qua tracker và HTTP.

Bên trong một tiến trình peer, `asyncio.Lock` bảo vệ thời điểm cập nhật danh sách chunk và checkpoint. Ghi dữ liệu dùng atomic replace để bảo đảm HTTP upload không đọc được phần file chưa hoàn tất. Các worker lấy chỉ số riêng từ queue nên không cùng chủ ý ghi một chunk tại cùng thời điểm.

Nếu người dùng gọi hai lần download cùng File ID khi tác vụ trước vẫn chạy, Peer API phản hồi HTTP 409 để tránh hai bộ worker tranh chấp cùng thư mục lưu trữ. Bộ test có tình huống đồng thời đăng ký hai nguồn tại tracker và từ chối tải trùng khi download đang chạy.

## 12. Tính nhất quán và đồng bộ trạng thái

Hệ thống sử dụng mô hình immutable content cho metadata: nội dung một File ID không được thay đổi trong quá trình chia sẻ. Nếu một thông báo cung cấp metadata khác cho File ID đang có, tracker trả lỗi xung đột. Chunk được xác minh theo mã băm bất biến và được bổ sung vào tập sở hữu của peer khi tải thành công.

Tập availability tại tracker đạt tính nhất quán cuối cùng (eventual consistency) với dữ liệu peer vì bản cập nhật được truyền qua HTTP và heartbeat định kỳ. Một chunk có thể đã được lưu tại peer nhưng chưa kịp công bố lên tracker; điều này chỉ làm chậm khám phá nguồn, không làm nội dung cuối sai.

Checkpoint được ghi nguyên tử, cùng với việc xác minh lại mọi chunk khi khởi động lại, ngăn chương trình tin tưởng một bản ghi cũ không còn đúng với dữ liệu trên đĩa. Việc hợp nhất theo index và xác minh hash cuối cùng làm kết quả đầu ra độc lập với thứ tự nhận các chunk trong mạng.

## 13. Mô hình lỗi và khả năng chịu lỗi

Các lỗi được xét gồm nguồn tạm dừng phục vụ, timeout kết nối, peer bị dừng đột ngột, chunk bị thay đổi ngoài mong đợi, tracker mất kết nối và tiến trình download bị đóng giữa chừng. Với kết nối thất bại, worker tải ghi log sự kiện RETRY, tra cứu lại nguồn và ưu tiên chọn peer chưa thử.

Trong tình huống nguồn A ngừng phục vụ khi B đang tải, B vẫn nhận các chunk còn thiếu từ C nếu C đã sở hữu các chunk đó. Khả năng phục hồi phụ thuộc vào tính sẵn có của bản sao; nếu chỉ một peer có chunk và peer đó offline, không thuật toán nào tự tạo lại dữ liệu bị mất.

Với chunk sai, downloader loại bỏ chunk không hợp lệ, tăng bộ đếm invalid_chunks và thử nguồn khác. Với tracker mất kết nối, các yêu cầu khám phá mới có thể thất bại; khi tracker hoạt động lại, peers sẽ đăng ký lại qua heartbeat. Thiết kế hiện tại không có tracker dự phòng nên tracker là một điểm lỗi đơn (single point of failure).

## 14. Cơ chế khôi phục và resume

Mỗi chunk hợp lệ được lưu dưới dạng file riêng trong `storage/<file_id>/`. Trạng thái metadata, danh sách các chunk đang sở hữu và cờ hoàn thành được lưu trong state.json. Thao tác ghi dùng file tạm và os.replace, hạn chế hỏng dữ liệu khi chương trình dừng bất thường giữa thao tác lưu.

Khi peer khởi động, nó duyệt các file state.json hiện có, phân tích metadata, đọc lại từng chunk được khai báo và kiểm tra mã băm thực tế. Chunk thiếu hoặc hỏng không được đưa vào tập khả dụng. Sau đó heartbeat định kỳ đăng ký lại các chunk hợp lệ với tracker.

Để tiếp tục tác vụ bị gián đoạn, người dùng gửi lại lệnh download cùng File ID. Hàng đợi chỉ chứa các chunk chưa có, tránh tải lại những phần đã được lưu và kiểm chứng. Một download đã hoàn thành có thể được kiểm tra hash lần nữa để khẳng định file đầu ra cục bộ còn đúng.

## 15. Bảo mật cơ bản

Tracker yêu cầu token riêng trong header X-Tracker-Token cho thao tác ghi availability. Các peer yêu cầu X-Peer-Token khi gửi chunk cho peer khác. Các API điều khiển seed, download, trạng thái riêng và debug được bảo vệ bằng X-Admin-Token. Token được cấp từ biến môi trường thay vì ghi cứng bí mật vào Git.

Seeder chỉ chấp nhận filename đơn không chứa đường dẫn, từ chối truy cập kiểu `../secret` hoặc đường dẫn tuyệt đối. Metadata xác thực định dạng hash, số chunk, kích thước file và kích thước chunk. Khi tải dữ liệu, downloader giới hạn số byte đọc từ peer, không tin tưởng Content-Length và không ghi dữ liệu sai hash vào kho chunk hợp lệ.

Giải pháp token chung và HTTP chưa mã hóa chỉ phù hợp demo trên mạng riêng. Nếu cần triển khai ngoài Internet, phải dùng TLS, nhận dạng peer bằng credential riêng, phân quyền truy cập tài nguyên cụ thể và biện pháp chống abuse/DoS. Không nên mở cổng admin ra Internet khi chưa có bảo mật bổ sung.

## 16. Hiện thực và triển khai

Mã nguồn được tổ chức thành package Python `p2psim`, với common.py, tracker.py, peer.py, cli.py. Dockerfile định nghĩa image Python 3.12 và cài các phụ thuộc chính. Compose triển khai tracker và ba peers trên một bridge network, cấu hình địa chỉ nội bộ và volume chứa trạng thái cho từng peer.

Chương trình demo được chạy theo thứ tự: tạo file mẫu, khởi động tracker, khởi động peers, seed file tại A, download về C, download về B từ hai nguồn, tạo file mới cho kịch bản swarm, sau đó tạo các file độc lập cho corruption và failure recovery. Việc dùng nhiều file khác nhau giúp các ca demo không bị tác động bởi checkpoint có sẵn từ ca trước.

Các lệnh chuẩn được ghi trong README.md. Nhóm có thể triển khai bằng VS Code trên Windows với Docker Desktop và Python, hoặc dùng Linux/macOS khi Docker Compose và Python phù hợp đã sẵn sàng. Toàn bộ hoạt động mạng được lưu vào log có timestamp và loại sự kiện để trình bày lúc vấn đáp.

Giao diện demo trực quan chạy bằng `python scripts/run_visual.py`, sau đó mở `http://127.0.0.1:8080`. Người trình bày chọn file thật từ máy (PDF, DOCX hoặc định dạng khác), rồi chọn một trong bốn demo hoặc chạy cả bốn. Chấp nhận file từ 8 byte đến 32 MiB, giữ nguyên tên và nội dung; kích thước chunk tự điều chỉnh theo `ceil(file_size / 64)` để có tối đa 64 chunk. Nút “Dùng file mẫu” quay về file 4 MiB gồm 64 chunk. Mỗi tình huống khởi tạo một tracker và ba peer bằng các tiến trình độc lập trên cổng loopback riêng, với thư mục mới `runtime/visual-<timestamp>-<uuid>/`. Chỉ A được cấp file nguồn; B và C nhận dữ liệu qua HTTP giữa các peer. C có bản sao chuẩn bị từ A trong demo 1, 3, 4; trong demo 2, C tham gia khi B đang tải dở.

Dashboard hiển thị kho chunk, hướng truyền theo sự kiện tải chunk thực, nhật ký và kết quả đối chiếu hash. File người dùng chọn được đưa vào thư mục riêng `runtime/visual-inputs/` của controller, sau đó chỉ sao chép vào shared của A cho từng lượt demo; không chèn thêm byte hay chuyển định dạng. Controller điều khiển và thu telemetry, không chuyển tiếp các lượt tải chunk giữa peer; tracker vẫn chỉ giữ metadata và availability. API `GET /api/debug/state` và `GET /api/debug/events` yêu cầu token quản trị. Mỗi peer giữ tối đa 2.048 sự kiện gần nhất trong bộ nhớ; token riêng của lượt chạy được giữ tại backend. File đã chọn dùng cho các lượt chạy trong cùng phiên dashboard, đặt lại khi khởi động lại chương trình. Sau khi đối chiếu hash độc lập, người dùng có thể tải file hoàn chỉnh của B về máy và mở bằng ứng dụng tương ứng. Dashboard chạy đến khi nhấn Ctrl+C, chỉ dừng các tiến trình do nó tạo và giữ lại file kết quả cùng log. Lệnh `python scripts/run_visual.py --check` chạy cùng bốn kịch bản để kiểm chứng rồi thoát; thêm `--file <đường dẫn>` để dùng tài liệu riêng. Hướng dẫn lấy minh chứng ở `docs/DEMO_VISUAL.md`.

## 17. Kiểm thử chức năng và đồng thời

Sau khi bổ sung chọn file ngày 19/10/2026 trên Windows, Python 3.14.4: bộ test đạt 47 bài trong 32,73 giây; bốn demo terminal vẫn thành công. PDF đặc tả được dùng cho đủ bốn demo với hash đầu ra khớp bản gốc. Chrome kiểm tra chọn DOCX hợp lệ có tên tiếng Việt, tải lại trang, chạy Demo 2 và tải file của B về máy, xác nhận nguyên tên và byte; file của C cũng khớp. File 8 byte chạy đủ bốn tình huống và file 32 MiB chạy Demo 1 thành công. Các kiểm thử TCP bổ sung bảo đảm nhận đầy đủ chunk lớn dù dữ liệu được chia thành nhiều đoạn vận chuyển và tiếp tục từ chối phản hồi quá cỡ. Chi tiết, giới hạn và ảnh ở `docs/TEST_PLAN.md` và `docs/DEMO_VISUAL.md`.

Kiểm chứng nền ngày 19/10/2026 trên Windows, Python 3.14.4, trước khi thêm chức năng chọn file và tải artifact: `python -m pytest -q` đạt 28 bài kiểm thử; `python scripts/run_local.py` và `python scripts/run_visual.py --check` đều chạy thành công cả bốn kịch bản mạng thật với file mẫu. Giao diện được kiểm tra bằng Chrome: Demo 3 đạt đủ điều kiện, không có lỗi JavaScript, không tràn ngang ở desktop và chiều rộng 390px. Demo 4 được kiểm tra lại sau khi siết điều kiện sự kiện, xác nhận A ngừng phục vụ và B nhận lại cùng chunk từ C. Xem `docs/TEST_PLAN.md` và `docs/DEMO_VISUAL.md` để đối chiếu phạm vi và minh chứng.

Bộ test có kiểm thử SHA-256, metadata hợp lệ/sai định dạng, filename nguy hiểm, chunk hợp lệ/chunk lỗi, lưu file nguyên tử, checkpoint JSON, file rỗng, peer announce, lease expiry, hai peer announce đồng thời, từ chối download trùng, chuyển file hai nguồn và phục hồi trạng thái sau khởi động lại.

Bài integration test tạo tiến trình tracker và ba tiến trình peer bằng subprocess, mỗi tiến trình sử dụng địa chỉ TCP và thư mục riêng. Dữ liệu kiểm thử chỉ được tạo vào shared directory của Peer A. Kiểm thử không đọc dữ liệu gốc trực tiếp qua process B/C; dữ liệu tải đi qua HTTP API chunk.

Kết quả kiểm thử tại môi trường thực hiện bản mẫu: `20 passed in 22.55s` khi chạy `PYTHONPATH=src pytest -q`. Ngoài ra, script demo chạy qua bốn tình huống bắt buộc và báo PASS cho cả bốn tình huống. Người nộp cần chạy lại trên máy nhóm, chụp minh chứng và cập nhật thời gian tương ứng trước khi nộp phiên bản cuối.

## 18. Kiểm thử lỗi, bảo mật và demo

Tình huống corrupted chunk: Seeder A chia file, Peer C tải đủ file, sau đó debug endpoint thay đổi byte đầu tiên của chunk index 1 ở C. Peer B tải file từ A/C; khi chọn C cho index 1, B phát hiện hash sai, từ chối nội dung và tải lại từ A. File hợp nhất cuối vẫn khớp file gốc.

Tình huống source failure: A và C đều sở hữu file, B bắt đầu tải song song; sau một số chunk đầu, API debug chuyển A sang trạng thái không phục vụ. Những request còn lại đến A nhận lỗi và được thử lại từ C. Script demo xác minh bộ đếm failed_requests và nguồn thực tế có bao gồm C.

Tình huống security: thiếu token tracker, thiếu token peer, truy cập API quản trị không xác thực và tên file chứa chuỗi traversal đều bị từ chối. Các log chứng minh API xử lý yêu cầu như thiết kế. Để mô phỏng lỗi crash-stop thực sự, có thể dừng container A khi B đang tải; kịch bản tự động dùng trạng thái 503 nhằm tạo lỗi có thể tái lập ổn định.

Trong demo trực quan, tiêu chí đạt được kiểm tra bằng dữ liệu sự kiện: demo 1 yêu cầu B dùng cả A và C; demo 2 yêu cầu B đã upload khi `completed=false` và C có nguồn B; demo 3 yêu cầu `INVALID_CHUNK` từ C cho index 1 rồi `DOWNLOAD_CHUNK` từ A cho cùng index; demo 4 đợi A có upload đang diễn ra, tắt phục vụ và xác nhận chunk cần thử lại được B nhận từ C. A vẫn chạy ở demo 4, trả HTTP 503 khi ngừng phục vụ và được bật lại khi dọn lỗi. Mọi demo đối chiếu kích thước và SHA-256 file cuối của B; demo 2 còn kiểm tra hash file cuối của C. Các tiêu chí này là cơ chế kiểm chứng của giao diện mới, không thay thế việc lưu kết quả chạy thực tế trên máy trình bày.

## 19. Đánh giá hiệu năng, giới hạn và hướng phát triển

Script performance.py đo thời gian hoàn thành và throughput tính theo số byte chia cho số giây tải. Hai cấu hình minh họa là tải tuần tự một worker và tải song song bốn worker, với kích thước chunk và mức trễ upload giữ theo cấu hình demo. Đây là phép đo để trình bày phương pháp đánh giá, chưa phải kết luận rằng bốn worker luôn nhanh hơn hoặc hệ thống có thể mở rộng tuyến tính.

Để thực nghiệm có giá trị hơn, cần thực hiện nhiều lần đo với dữ liệu lưu trữ và khả năng sẵn có của các nguồn được giữ nhất quán, thu CPU/RAM/băng thông, lấy trung bình và độ lệch, đồng thời ghi đầy đủ cấu hình máy. Khi một peer đã tải sẵn file từ vòng trước, số nguồn tăng sẽ làm phép so sánh lệch; vì thế nên reset volumes trước các lượt đo được dùng để kết luận.

Giới hạn chính: tracker chỉ có một instance và lưu trạng thái trong RAM; chưa hỗ trợ DHT, rarest-first, choking/unchoking, NAT traversal, TLS hoặc xác thực per-peer theo PKI. Hệ thống chưa chứng minh khả năng chịu tải trên WAN thực. Có thể nâng cấp bằng replicated tracker, chiến lược tải rarest-first, giới hạn băng thông, quản lý quyền truy cập chi tiết, và mô phỏng churn ở quy mô hàng chục peer.

## 20. Kết luận, tài liệu và phụ lục

Đề tài đã thể hiện các thành phần quan trọng của hệ phân tán: nhiều tiến trình độc lập liên lạc qua mạng, peer discovery bằng tracker, truyền chunk trực tiếp giữa peer, tải và upload đồng thời, xác minh dữ liệu, tính nhất quán eventual và phục hồi khi nguồn bị gián đoạn. Các nội dung này đáp ứng mục tiêu tạo một mô hình BitTorrent-like ở mức đồ án đại học thay vì sao chép một sản phẩm BitTorrent hoàn chỉnh.

Phiên bản có chọn file đã đạt 47 bài kiểm thử, bốn kịch bản với PDF và các kiểm chứng trình duyệt trên môi trường Windows nêu ở mục 17; vẫn cần chạy lại trên máy dùng để trình bày. Phiên bản cuối cần bổ sung tên và đóng góp thật của từng thành viên, URL kho GitHub, hình chụp Docker/log, các số đo hiệu năng và bảng phân công. Những bổ sung này không làm thay đổi logic nền tảng đã hiện thực.

Tài liệu cơ sở: (1) COURSE PROJECT SPECIFICATION, môn Distributed Systems, mục 4, 6, 9–13 (tài liệu môn học đính kèm ở `docs/input_specification.pdf`); (2) Tài liệu thư viện Python chuẩn, FastAPI, aiohttp, Docker Compose và pytest, nếu nhóm sử dụng trong trích dẫn cuối cùng thì điền link và ngày truy cập xác thực.

Phụ lục source code và kiểm thử: `README.md`, `docs/ARCHITECTURE.md`, `docs/TEST_PLAN.md`, `src/p2psim/`, `tests/`, `scripts/`, `compose.yaml`, `.env.example`. Xem các file này để tái lập chương trình và truy vết từng bài test.
