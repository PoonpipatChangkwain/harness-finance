"use client";

/**
 * CSV import (phase-13 §13.2): drop/pick a file → preview it in the browser (papaparse) →
 * `POST /portfolios/{id}/import` for the server preview (symbols resolved, value rows
 * converted, per-row errors) → Commit (`?commit=1`). Commit replaces the positions.
 */

import { clsx } from "clsx";
import { FileUp, X } from "lucide-react";
import Papa from "papaparse";
import { useRef, useState, type DragEvent } from "react";

import { ApiError } from "@/lib/api";
import { entryErrors, portfolioApi, type EntryError, type ImportPreview } from "@/lib/portfolio-api";
import { formatQty } from "@/lib/portfolio-format";

export const CSV_TEMPLATE = "symbol,quantity,avg_cost,weight,note\nPTT,1000,32.5,,\nAAPL,10,180,,US\n";
const PREVIEW_ROWS = 20;

export interface ImportApi {
  preview: (id: string, csv: string) => Promise<ImportPreview>;
  commit: (id: string, csv: string) => Promise<unknown>;
}

const defaultApi: ImportApi = { preview: portfolioApi.importPreview, commit: portfolioApi.importCommit };

export function parseCsvPreview(text: string): { header: string[]; rows: Record<string, string>[]; errors: string[] } {
  const parsed = Papa.parse<Record<string, string>>(text.replace(/^﻿/, ""), {
    header: true,
    skipEmptyLines: "greedy",
    transformHeader: (h) => h.trim().toLowerCase(),
  });
  return {
    header: parsed.meta.fields ?? [],
    rows: parsed.data,
    errors: parsed.errors.map((e) => `แถว ${(e.row ?? 0) + 1}: ${e.message}`),
  };
}

export function ImportCsvDialog({
  portfolioId,
  onClose,
  onCommitted,
  api = defaultApi,
}: {
  portfolioId: string;
  onClose: () => void;
  onCommitted: () => void;
  api?: ImportApi;
}) {
  const [text, setText] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [server, setServer] = useState<ImportPreview | null>(null);
  const [errors, setErrors] = useState<EntryError[]>([]);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const local = text !== null ? parseCsvPreview(text) : null;

  const load = async (file: File) => {
    setFileName(file.name);
    setServer(null);
    setErrors([]);
    setFailure(null);
    const content = await file.text();
    setText(content);
    setBusy(true);
    try {
      const preview = await api.preview(portfolioId, content);
      setServer(preview);
      setErrors(preview.errors);
    } catch (err) {
      setFailure(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const commit = async () => {
    if (text === null) return;
    setBusy(true);
    try {
      await api.commit(portfolioId, text);
      onCommitted();
      onClose();
    } catch (err) {
      const rows = entryErrors(err);
      if (rows) setErrors(rows);
      else setFailure(err instanceof ApiError || err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) void load(file);
  };

  return (
    <div role="dialog" aria-modal="true" aria-label="Import CSV" className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4">
      <div data-testid="import-dialog" className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-lg border border-line-strong bg-layer-1 shadow-xl">
        <div className="flex items-center border-b border-line px-4 py-2">
          <h2 className="text-[14px] font-semibold">Import CSV</h2>
          <span className="flex-1" />
          <button type="button" aria-label="ปิด" onClick={onClose} className="rounded-md p-1 text-muted hover:bg-layer-2">
            <X className="size-4" aria-hidden />
          </button>
        </div>
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4 text-[12.5px]">
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            onClick={() => input.current?.click()}
            className={clsx(
              "flex cursor-pointer flex-col items-center gap-1 rounded-md border border-dashed px-4 py-6 text-center",
              dragging ? "border-accent bg-accent-muted/30" : "border-line-strong",
            )}
          >
            <FileUp className="size-5 text-muted" aria-hidden />
            <span>{fileName ?? "ลากไฟล์ CSV มาวาง หรือคลิกเพื่อเลือก"}</span>
            <span className="num text-[11px] text-subtle">symbol,quantity,avg_cost,weight,note (+ value,pnl_pct,target_weight)</span>
            <input
              ref={input}
              type="file"
              accept=".csv,text/csv"
              data-testid="csv-file"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void load(f);
              }}
            />
          </div>
          <p className="text-[11.5px] text-subtle">
            weight / pnl_pct รับได้ทั้ง <span className="num">15%</span>, <span className="num">15</span> หรือ{" "}
            <span className="num">0.15</span> · Commit จะแทนที่ positions ทั้งหมดของพอร์ตนี้ (เงินสดคงเดิม) ·{" "}
            <a
              className="text-accent"
              download="portfolio-template.csv"
              href={`data:text/csv;charset=utf-8,${encodeURIComponent(CSV_TEMPLATE)}`}
            >
              ดาวน์โหลดตัวอย่าง
            </a>
          </p>

          {local ? (
            <div>
              <div className="mb-1 text-[11.5px] text-muted">
                ไฟล์: {local.rows.length} แถว{local.rows.length > PREVIEW_ROWS ? ` (แสดง ${PREVIEW_ROWS} แถวแรก)` : ""}
              </div>
              <div className="max-h-48 overflow-auto rounded-md border border-line">
                <table data-testid="csv-preview" className="num w-full text-[12px]">
                  <thead className="bg-layer-2 text-left text-subtle">
                    <tr>
                      <th className="px-2 py-1 font-normal">#</th>
                      {local.header.map((h) => (
                        <th key={h} className="px-2 py-1 font-normal">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {local.rows.slice(0, PREVIEW_ROWS).map((r, i) => {
                      const bad = errors.some((e) => e.index === i);
                      return (
                        <tr key={i} className={clsx("border-t border-line/60", bad && "bg-error/10 text-error")}>
                          <td className="px-2 py-0.5 text-subtle">{i + 1}</td>
                          {local.header.map((h) => (
                            <td key={h} className="px-2 py-0.5">
                              {r[h]}
                            </td>
                          ))}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {local.errors.map((e) => (
                <p key={e} className="text-warning">
                  {e}
                </p>
              ))}
            </div>
          ) : null}

          {busy ? <p className="text-subtle">กำลังตรวจสอบกับ server…</p> : null}
          {failure ? <p className="text-error">{failure}</p> : null}
          {server ? (
            <div data-testid="server-preview" className="space-y-1">
              <p>
                server: อ่านได้ <span className="num">{server.positions.length}</span> / {server.rows} positions
                {server.converted.length ? ` · แปลงมูลค่าเป็นจำนวนหน่วย ${server.converted.length} ตัว` : ""}
              </p>
              {server.converted.map((c) => (
                <p key={String(c.symbol)} className="num text-[11.5px] text-muted">
                  {String(c.symbol)}: ราคา {formatQty(String(c.price))} → {formatQty(String(c.units))} หน่วย @ {formatQty(String(c.avg_cost))}
                </p>
              ))}
            </div>
          ) : null}
          {errors.length ? (
            <ul data-testid="import-errors" className="space-y-0.5 rounded-md bg-error/10 p-2 text-error">
              {errors.map((e, i) => (
                <li key={i}>
                  {e.index !== null ? `แถว ${e.index + 1}` : "ทั้งไฟล์"}
                  {e.symbol ? ` (${e.symbol})` : ""}: {e.message}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
        <div className="flex justify-end gap-2 border-t border-line px-4 py-2">
          <button type="button" onClick={onClose} className="rounded-md px-3 py-1 text-[13px] text-muted hover:bg-layer-2">
            ยกเลิก
          </button>
          <button
            type="button"
            data-testid="commit-import"
            disabled={busy || !server || errors.length > 0 || server.positions.length === 0}
            onClick={() => void commit()}
            className="rounded-md bg-accent px-3 py-1 text-[13px] text-bg disabled:cursor-not-allowed disabled:opacity-50"
          >
            Commit
          </button>
        </div>
      </div>
    </div>
  );
}
