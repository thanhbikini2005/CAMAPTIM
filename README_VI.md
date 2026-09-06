# BOT QUÉT CÁ MẬP + WYCKOFF (GitHub Actions)

Bot này được port từ logic trong `LuDanDaoGam_Wyckoff_fixed_v3` và quét **696 mã** trong `symbols.txt`.

## Bot bắt tín hiệu gì?

### Main – Cá mập
Điều kiện bắt buộc trước khi tô màu:
- A1: Low hiện tại < hỗ trợ thấp nhất của **20 nến trước**.
- A2: Close hiện tại > hỗ trợ đó (thủng rồi đóng lại trên hỗ trợ).
- Không bị `blocked`: **CMF20 > 0** và **Trend Filter = true**.

Trend Filter đúng khi có ít nhất 1 trong 3:
1. Double Bottom: 2 pivot low cách 5–30 nến, lệch đáy <=2.5%, close hiện tại vượt neckline.
2. Phân kỳ RSI dương: đáy sau thấp hơn nhưng RSI sau cao hơn.
3. Vượt MA20 kèm volume > 1.3 x Volume MA20.

B1–B6:
- B1: râu dưới / thân >= 0.5.
- B2: close nằm >=70% biên nến tính từ Low.
- B3: Volume/MA20 trong [1.5x, 3.0x].
- B4: CMF20 > 0.
- B5: MFI14 > 55.
- B6: rủi ro từ close đến stop <=7%, với stop = Low x (1 - 7%/2).

Màu:
- 🟣 **Tím**: A1 + A2 + không blocked + đạt **>=7/8** A1..B6.
- 🟡 **Vàng**: A1 + A2 + không blocked + đạt **5–6/8**.
- 🟢 **Xanh**: A1 + A2 + không blocked + đạt **<5/8**.

### Wyckoff
Bot dùng 60 nến và đúng logic code:
- W1 Phase B: Close cách POC60 <=5%.
- W2 Spring Break: Low < S_min 60 nến trước và Close > S_min.
- W3 Spring Volume: W2 đúng và Volume <= Volume MA20.
- W4 RSI: W2 đúng + phân kỳ RSI pivot dương (chỉ cộng điểm).
- **Spring = W2 AND W3**. W4 không bắt buộc để `isSpring=true`.
- W5 Markup: cặp Weis Wave hoàn tất cuối cùng là Up -> Down và sóng Up có cả Volume lẫn delta giá lớn hơn sóng Down.
- **Upthrust**: High phá resistance 60 nến trước, Close đóng lại dưới resistance, và (Weis effort-failure hoặc Volume >=1.5 x MA20).

## Cài đặt cho người không biết code

### Bước 0 – rất quan trọng: đổi Telegram token
Token đã từng được dán trong chat. Hãy vào **BotFather** trên Telegram và tạo/regenerate token mới trước khi dùng. Không đưa token mới vào file code hoặc chat công khai.

### Bước 1 – tạo repository GitHub
1. Vào GitHub -> **New repository**.
2. Đặt tên ví dụ: `bot-quet-ca-map`.
3. Chọn **Private** nếu chỉ dùng cá nhân.
4. Bấm **Create repository**.

### Bước 2 – upload các file trong gói này
Upload toàn bộ nội dung, giữ đúng cấu trúc:

```text
bot-quet-ca-map/
├─ bot.py
├─ symbols.txt
├─ requirements.txt
├─ .gitignore
└─ .github/
   └─ workflows/
      └─ shark-scan.yml
```

### Bước 3 – tạo GitHub Secrets
Trong repository:
1. **Settings** -> **Secrets and variables** -> **Actions**.
2. Bấm **New repository secret**.
3. Tạo secret `TELEGRAM_BOT_TOKEN` = token Telegram **mới**.
4. Tạo secret `TELEGRAM_CHAT_ID` = `1242874545`.

### Bước 4 – test Telegram trước
1. Mở tab **Actions**.
2. Chọn **Shark & Wyckoff Scanner**.
3. Bấm **Run workflow**.
4. Ở `mode`, chọn **telegram_test**.
5. Bấm Run.
6. Telegram phải nhận: `Test BOT QUÉT CÁ MẬP: Telegram đã kết nối thành công.`

### Bước 5 – chạy quét thật
Làm lại **Run workflow**, nhưng chọn `mode = scan`.
Bot sẽ:
- tải dữ liệu D1;
- tính RSI14, CMF20, MFI14, MA20 Volume;
- tính A1–B6 + Trend Filter;
- tính POC60, Weis Wave, Spring, Upthrust;
- nhóm 🟣/🟡/🟢/🌱/⚠️;
- gửi kết quả sang Telegram;
- lưu `scan_report.txt` trong phần Artifacts của lần chạy GitHub Actions.

### Bước 6 – tự động chạy
Workflow đã cài sẵn:
- **15:35 giờ Việt Nam, thứ 2–thứ 6**.
- GitHub dùng UTC nên cron là `35 8 * * 1-5`.

Nếu muốn đổi giờ, chỉ sửa dòng cron trong `.github/workflows/shark-scan.yml`.

## Nguồn dữ liệu
Bot thử theo thứ tự:
1. TCBS public bars API – cùng họ nguồn với web tool.
2. VNDIRECT `stock_prices` fallback.
3. Yahoo Finance `.VN` fallback.

Một số mã trong danh sách có thể mới, đổi mã, không còn giao dịch hoặc provider chưa hỗ trợ; bot sẽ ghi vào nhóm lỗi nhưng vẫn quét tiếp các mã khác.

## Lưu ý kỹ thuật
- Bot chỉ phát tín hiệu trên **nến D1 mới nhất**.
- Không lưu Telegram token trong repository.
- Telegram giới hạn độ dài tin nhắn; bot tự chia nhiều tin nếu kết quả dài.
- Đây là bộ lọc theo rule của file nguồn, không phải đảm bảo lợi nhuận hay khuyến nghị mua/bán.
