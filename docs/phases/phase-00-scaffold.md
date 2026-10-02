# Phase 00 — Scaffold & tooling

## เป้าหมาย
สร้างโครง `backend/` + `frontend/` ที่รัน, lint, test และ typecheck ได้ พร้อมบังคับกฎ **Harness ≠ Domain (F2)** ด้วย `import-linter` ตั้งแต่วันแรก

## ต้องเสร็จก่อน
– (phase แรก)

## อ่านก่อนเริ่ม
- `Product/CLAUDE.md` (โครงสร้างโฟลเดอร์)
- `Product/docs/contracts.md` §1 (ทิศทางการ import), §12 (storage)
- `../TradingAgents/pyproject.toml`, `../TradingAgents/.env.example` (ดูว่าจัด deps/env อย่างไร)
- `../dexter/env.example`

## Tasks

### 0.1 Root
- `Product/.gitignore`: `.env`, `backend/.data/`, `.venv/`, `node_modules/`, `.next/`, `__pycache__/`, `*.db`, `coverage/`
- `Product/.env.example`: รวม LLM keys (Phase 04) + data keys ([data-sources.md](../data-sources.md) §4) + `DATA_DIR=./.data`, `APP_ENV=dev`, `FRONTEND_ORIGIN=http://localhost:3000`, `NEXT_PUBLIC_API_BASE=http://localhost:8000/api`
- `Product/justfile` (หรือ `package.json` scripts ที่ root ถ้าไม่อยากพึ่ง `just`) มีคำสั่ง: `dev` (รัน backend + frontend พร้อมกัน), `test`, `lint`, `typecheck`, `fmt`
- `git init` ใน `Product/` **ห้ามทำ** จนกว่าผู้ใช้จะสั่ง

### 0.2 Backend (`Product/backend/`)
- ใช้ `uv` + `pyproject.toml` (Python 3.12) ชื่อ package `app`
- deps ตาม group
  - runtime: `fastapi`, `uvicorn[standard]`, `sse-starlette`, `pydantic>=2.8`, `pydantic-settings`, `sqlalchemy>=2.0`, `aiosqlite`, `alembic`, `httpx`, `python-ulid`, `pandas`, `numpy`, `pyyaml`
  - เพิ่มใน phase ที่ใช้เท่านั้น (**ยังไม่ต้องติดตั้ง**): `yfinance` (P02), `langchain-core` + provider packages (P04), `langgraph` + `langgraph-checkpoint-sqlite` (P09)
  - dev: `pytest`, `pytest-asyncio`, `pytest-cov`, `respx`, `freezegun`, `ruff`, `mypy`, `import-linter`, `pandas-stubs`, `types-PyYAML`
- สร้างโครงโฟลเดอร์ตาม CLAUDE.md ให้ครบ (มี `__init__.py` ทุกโฟลเดอร์ย่อย) และสร้าง `domain/ports.py` เป็นไฟล์ว่างที่มี docstring
- `app/settings.py`: `class Settings(BaseSettings)` อ่าน `.env` (ทุก key เป็น `SecretStr | None`) + `data_dir: Path` และมีฟังก์ชัน `get_settings()` ที่ cache ด้วย `lru_cache`
- `app/api/main.py`: FastAPI app, CORS (`FRONTEND_ORIGIN`), router `/api`, `GET /api/health → {"status":"ok","version":...}` และ exception handler ที่คืน `{error:{type,message}}`
- `app/harness/storage/db.py`: async engine (`sqlite+aiosqlite:///{data_dir}/app.db`), session factory และ `Base` (DeclarativeBase) ส่วน Alembic init ใน `backend/alembic/` พร้อม migration แรกที่ว่างไว้ก่อน
- `pyproject.toml` ตั้งค่า:
  - ruff: `line-length=110`, rules `E,F,I,B,UP,SIM,RUF,ASYNC`
  - mypy: `strict = true` สำหรับ `app.domain.*` และ `app.harness.*`
  - pytest: `asyncio_mode=auto`, markers `live`
  - **import-linter contracts**:
    ```toml
    [tool.importlinter]
    root_package = "app"
    [[tool.importlinter.contracts]]
    name = "Domain is framework-free (F2)"
    type = "forbidden"
    source_modules = ["app.domain"]
    forbidden_modules = ["langgraph", "langchain", "langchain_core", "fastapi", "sqlalchemy", "app.harness", "app.api"]
    [[tool.importlinter.contracts]]
    name = "Harness does not import API"
    type = "forbidden"
    source_modules = ["app.harness"]
    forbidden_modules = ["app.api"]
    ```
- `tests/test_health.py` ใช้ `httpx.AsyncClient` + `ASGITransport`

### 0.3 Frontend (`Product/frontend/`)
- `pnpm create next-app` (TypeScript, App Router, Tailwind, ESLint, `src/` = ไม่ใช้, import alias `@/*`)
- ติดตั้ง shadcn/ui (`pnpm dlx shadcn@latest init`) + components: button, input, textarea, dialog, sheet, tabs, tooltip, popover, dropdown-menu, select, badge, scroll-area, separator, table, command, sonner, skeleton, switch, checkbox, resizable
- deps: `zustand`, `@microsoft/fetch-event-source`, `react-markdown`, `remark-gfm`, `lucide-react`, `clsx`, `react-json-view-lite`, `zod`, `react-hook-form`, `@hookform/resolvers` (ส่วน `lightweight-charts`, `recharts`, `@xyflow/react` และ `@tanstack/react-table` ให้ติดตั้งใน phase ที่ใช้)
- dev: `vitest`, `@testing-library/react`, `@testing-library/jest-dom`, `jsdom`, `openapi-typescript`
- `tsconfig.json`: `strict: true`, `noUncheckedIndexedAccess: true`
- `app/globals.css`: ใส่ design tokens `--fh-*` ตาม [ui-spec.md §2](../ui-spec.md#2-design-tokens) ทั้ง light และ dark แล้ว map เข้า Tailwind theme
- ฟอนต์: `IBM_Plex_Sans_Thai` + `JetBrains_Mono` ผ่าน `next/font/google`
- `app/page.tsx` redirect ไป `/research` และสร้าง placeholder page ของ `/research`, `/trade`, `/portfolio`, `/settings` (ใส่แค่ title)
- `lib/api.ts`: `apiFetch<T>(path, init)` ที่อ่าน `NEXT_PUBLIC_API_BASE` และ parse error shape
- scripts: `dev`, `build`, `lint`, `typecheck` (`tsc --noEmit`), `test` (`vitest run`), `gen:api` (`openapi-typescript http://localhost:8000/openapi.json -o lib/api-types.ts`)
- test แรก: render หน้า research placeholder

## Definition of Done
- [ ] `uv run uvicorn app.api.main:app --reload` แล้ว `GET /api/health` ได้ 200
- [ ] `uv run pytest`, `uv run ruff check .`, `uv run mypy app`, `uv run lint-imports` ผ่านทั้งหมด
- [ ] ทดลองเพิ่ม `import fastapi` ใน `app/domain/x.py` ชั่วคราวแล้ว `lint-imports` ต้อง **fail** จากนั้นลบไฟล์ทิ้ง
- [ ] `pnpm dev` เปิด `http://localhost:3000/research` ได้ และสลับ dark/light ได้
- [ ] `pnpm lint`, `pnpm typecheck`, `pnpm test` ผ่าน
- [ ] คำสั่งทั้งหมดรันได้บน Windows PowerShell
- [ ] อัปเดต `## Commands` ใน `Product/CLAUDE.md`

## นอกขอบเขต
การใช้ LLM, data provider, UI จริง และ Docker
