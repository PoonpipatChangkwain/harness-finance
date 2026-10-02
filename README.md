# Finance Harness (Product)

รายละเอียดสถาปัตยกรรม/แผนงานอยู่ที่ [CLAUDE.md](CLAUDE.md) — ไฟล์นี้มีแค่วิธีติดตั้งและรัน

## Prerequisites

- Python 3.12 + [uv](https://docs.astral.sh/uv/)
- Node.js + [pnpm](https://pnpm.io/) (frontend)
- Node.js + npm (สำหรับ script รวมที่ root — ต้องมี `concurrently`)

## ติดตั้งครั้งแรก

```bash
# root (สำหรับ npm run dev ที่รันทั้งคู่พร้อมกัน)
npm install

# backend
cd backend
uv sync
cp .env.example ../.env   # ถ้ายังไม่มี .env แล้วกรอก API key ของ provider ที่จะใช้
cd ..

# frontend
cd frontend
pnpm install
cd ..
```

## รันแบบ dev

**รันทั้งคู่พร้อมกัน (จาก `Product/`):**

```bash
npm run dev
```

**หรือรันแยก คนละ terminal:**

```bash
npm run dev:backend    # http://localhost:8000  (มี migration อัตโนมัติ)
npm run dev:frontend   # http://localhost:3000
```

## คำสั่งอื่นๆ

| งาน | คำสั่ง |
|-----|--------|
| test ทั้งคู่ | `npm run test` |
| test backend อย่างเดียว | `npm run test:backend` |
| test frontend อย่างเดียว | `npm run test:frontend` |
| lint ทั้งคู่ | `npm run lint` |
| typecheck ทั้งคู่ | `npm run typecheck` |
| format backend | `npm run fmt:backend` |

คำสั่งเฉพาะ backend เพิ่มเติม (migration, import-lint, LLM smoke test ฯลฯ) อยู่ในตาราง **Commands** ของ [CLAUDE.md](CLAUDE.md#L339-L357)

## Troubleshooting

**`WinError 10013: An attempt was made to access a socket in a way forbidden by its access permissions`**
เกิดตอนรัน backend บนพอร์ต 8000 — สาเหตุมักเป็นพอร์ตอยู่ใน Windows excluded port range (Hyper-V/WSL จองไว้) หรือถูก firewall/โปรแกรมอื่นบล็อก ไม่ใช่พอร์ตชนกันตรงๆ

ตรวจสอบ:
```powershell
netsh interface ipv4 show excludedportrange protocol=tcp
```
ถ้า 8000 อยู่ในช่วงที่ถูกจอง ให้เปลี่ยนพอร์ต เช่น:
```bash
uv run --directory backend python -m uvicorn app.api.main:app --reload --port 8010
```
(แล้วอัปเดต proxy/base URL ฝั่ง frontend ให้ตรงกับพอร์ตใหม่ด้วย)

**launcher ใน `.venv` ชี้ไป interpreter ผิด** (เช่น `pytest.exe`, `uvicorn.exe` พังหลัง copy `.venv` มาจากเครื่องอื่น) — แก้ด้วย:
```bash
cd backend && uv sync --reinstall
```
