# 🎓 OLTool - Onluyen Automation & AI Tutor (Standalone)

> **⚠️ TUYÊN BỐ MIỄN TRỪ TRÁCH NHIỆM:**  
> Dự án phục vụ mục đích nghiên cứu kỹ thuật và hỗ trợ học tập cá nhân. **Tác giả không chịu bất cứ trách nhiệm nào đối với kết quả làm bài, tài khoản hay điểm số của các bạn trên hệ thống Onluyen.**

---

## 🌟 Giới thiệu

**OLTool** là phiên bản rút gọn, chạy trực tiếp trên **1 máy tính duy nhất** (Windows, macOS, Linux) không phụ thuộc máy chủ trung gian, không yêu cầu cài đặt bot hay hạ tầng mạng phức tạp.

### Tính năng nổi bật:
- 🚀 **Tự động đăng nhập & Tìm bài**: Tự động nhận diện bài tập mới giao trên Dashboard hoặc nhận link trực tiếp.
- 🥷 **Chống phát hiện (Stealth & Anti-cheat Spoofing)**:
  - Giả lập vân tay trình duyệt ngẫu nhiên (Canvas noise, WebGL context, Audio buffer, Hardware concurrency).
  - Tự động bỏ qua các cờ `navigator.webdriver`.
- ⏱️ **Mô phỏng hành vi học sinh (Human Pacing)**:
  - Thời gian suy nghĩ ngẫu nhiên theo độ khó từng câu hỏi (30s - 90s).
  - Di chuyển chuột, độ trễ gõ phím tự nhiên, không giải đồng loạt.
- 🎯 **Kiểm soát điểm số thông minh (Score Planner)**:
  - Đặt trước điểm mong muốn (ví dụ 8.5đ, 9.0đ).
  - Tự động phân bổ câu đúng/sai theo đúng ma trận đề thi chuẩn Bộ GD&ĐT 2025.
- 📚 **Chế độ Gia Sư (Tutor Mode)**:
  - Quét đề thi, giải và xuất lời giải chi tiết từng bước ra file `loigiai.md` mà **không nộp bài hay can thiệp vào trang thi**.
- 🤖 **Hỗ trợ mọi mô hình AI chuẩn OpenAI**:
  - Tương thích OpenAI (`gpt-4o-mini`, `gpt-4o`), Google Gemini (qua endpoint tương thích OpenAI), DeepSeek, Claude hoặc Ollama cục bộ.
  - **Không đóng gói kèm bất kỳ API server hay cookie riêng tư nào**.

---

## 🚀 Hướng dẫn cài đặt nhanh

### 1. Windows (Click-and-Run)
1. Cài đặt [Python 3.10 trở lên](https://www.python.org/downloads/) (Nhớ tích chọn **"Add Python to PATH"** lúc cài).
2. Tải hoặc giải nén thư mục `oltool`.
3. Nhấp đúp chuột vào file **`install.bat`** (hoặc mở PowerShell gõ `.\install.ps1`).
4. Mở file **`config.json`** vừa tạo, điền thông tin tài khoản và API Key của bạn.
5. Nhấp đúp chuột vào file **`run.bat`** (hoặc `.\run.ps1`) để bắt đầu!

---

### 2. Linux & macOS
1. Mở Terminal và điều hướng vào thư mục dự án:
   ```bash
   cd oltool
   ```
2. Phân quyền và chạy script cài đặt:
   ```bash
   chmod +x install.sh run.sh
   ./install.sh
   ```
3. Chỉnh sửa cấu hình trong file `config.json`:
   ```bash
   nano config.json
   ```
4. Chạy công cụ:
   ```bash
   ./run.sh
   ```

---

## ⚙️ Cấu hình `config.json`

```json
{
  "username": "email_hoc_sinh@haiphong.edu.vn",
  "password": "mat_khau_cua_ban",
  "test_url": null,
  "target_score": 8.5,
  "pacing": "stealth",
  "mode": "auto",
  "ai": {
    "base_url": "https://api.openai.com/v1",
    "api_key": "sk-your-openai-or-compatible-api-key",
    "model": "gpt-4o-mini",
    "timeout": 60
  }
}
```

### Các thông số chính:
| Tham số | Ý nghĩa | Mặc định |
| :--- | :--- | :--- |
| `username` | Tên đăng nhập Onluyen (Email hoặc SĐT) | Bắt buộc |
| `password` | Mật khẩu tài khoản Onluyen | Bắt buộc |
| `test_url` | Link trực tiếp bài thi (để `null` để tự tìm bài mới nhất) | `null` |
| `target_score` | Điểm mục tiêu từ 0.0 đến 10.0 | `8.5` |
| `pacing` | Tốc độ làm bài: `stealth` (chậm/an toàn), `balanced`, `fast` | `stealth` |
| `mode` | `auto` (làm & nộp bài) hoặc `tutor` (chỉ tạo lời giải chi tiết) | `auto` |
| `ai.base_url` | Endpoint tương thích OpenAI | `https://api.openai.com/v1` |
| `ai.api_key` | API Key của nhà cung cấp AI | Bắt buộc |
| `ai.model` | Tên mô hình AI | `gpt-4o-mini` |

---

## 🖥️ Sử dụng nâng cao qua lệnh dòng (CLI)

Bạn có thể ghi đè bất kỳ tham số nào trực tiếp từ terminal:

```bash
# Chạy với điểm mục tiêu 9.0 và hiện cửa sổ trình duyệt để quan sát
./run.sh --target-score 9.0 --headed

# Chế độ Gia sư (tạo lời giải ra file loigiai.md)
./run.sh --mode tutor

# Chỉ định link bài thi cụ thể
./run.sh --url "https://app.onluyen.vn/school/test/step/xxxxxx"
```

---

## 📄 Bản quyền & Đóng góp

- Tác giả: **Trần Nguyên Minh Nhật**
- Giấy phép: MIT License.
