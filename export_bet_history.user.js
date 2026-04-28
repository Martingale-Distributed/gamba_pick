// ==UserScript==
// @name         Pick Sites - Export Bet History
// @namespace    https://github.com/lothrop/gamba_pick
// @version      1.1
// @description  Export bet history from *pick sites to CSV or raw JSON via localforage/IndexedDB
// @author       lothrop
// @match        https://tonpick.game/*
// @match        https://tronpick.io/*
// @match        https://litepick.io/*
// @match        https://polpick.io/*
// @match        https://bnbpick.io/*
// @match        https://dogepick.io/*
// @match        https://solpick.io/*
// @match        https://suipick.io/*
// @grant        none
// ==/UserScript==

(function () {
    "use strict";

    const SITE_CURRENCIES = {
        "tonpick.game": "TON",
        "tronpick.io": "TRX",
        "litepick.io": "LTC",
        "polpick.io": "POL",
        "bnbpick.io": "BNB",
        "dogepick.io": "DOGE",
        "solpick.io": "SOL",
        "suipick.io": "SUI",
    };

    const currency = SITE_CURRENCIES[location.hostname] || location.hostname;

    // Read from IndexedDB directly (works even if localforage JS isn't loaded yet)
    function readIndexedDB() {
        return new Promise((resolve) => {
            const req = indexedDB.open("localforage");
            req.onsuccess = (event) => {
                const db = event.target.result;
                try {
                    const tx = db.transaction("keyvaluepairs", "readonly");
                    const store = tx.objectStore("keyvaluepairs");

                    const large = store.get("my_bet_data_large");
                    const small = store.get("my_bet_data");

                    tx.oncomplete = () => {
                        db.close();
                        resolve({
                            large: large.result || null,
                            small: small.result || null,
                        });
                    };
                    tx.onerror = () => {
                        db.close();
                        resolve(null);
                    };
                } catch (e) {
                    db.close();
                    resolve(null);
                }
            };
            req.onerror = () => resolve(null);
            req.onupgradeneeded = (event) => {
                event.target.transaction.abort();
                resolve(null);
            };
        });
    }

    // Try localforage first (if available), fall back to raw IndexedDB
    async function getBetData() {
        if (typeof localforage !== "undefined") {
            try {
                const large = await localforage.getItem("my_bet_data_large");
                const small = await localforage.getItem("my_bet_data");
                if (large || small) return large || small;
            } catch (e) {
                console.log("[BetExport] localforage read failed, trying IndexedDB:", e);
            }
        }
        const data = await readIndexedDB();
        if (!data) return null;
        return data.large || data.small || null;
    }

    function stripHtml(str) {
        return String(str).replace(/<[^>]+>/g, "");
    }

    function formatTimestamp(ts) {
        try {
            const d = new Date(Number(ts) * 1000);
            if (isNaN(d.getTime())) return String(ts);
            const pad = (n) => String(n).padStart(2, "0");
            return (
                d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
                " " + pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds())
            );
        } catch (e) {
            return String(ts);
        }
    }

    function parseEntries(rawEntries) {
        const entries = [];
        for (const item of rawEntries) {
            try {
                const game = item.game_name || "Unknown";
                const bet = parseFloat(
                    String(item.bet_amount || "0").replace(/,/g, "").trim()
                );
                const payoutStr = stripHtml(
                    String(item.payout || "0").replace(/\u00d7/g, "").replace(/x/g, "").replace(/,/g, "")
                ).trim();
                const multiplier = parseFloat(payoutStr) || 0;
                const profitStr = stripHtml(
                    String(item.profit || "0").replace(/,/g, "")
                ).trim();
                const profit = parseFloat(profitStr) || 0;
                const time = formatTimestamp(item.time || item.timestamp || "");

                entries.push({ time, game, bet, multiplier, profit });
            } catch (e) {
                console.log("[BetExport] Skipping entry:", e, item);
            }
        }
        // Sort newest first
        entries.sort((a, b) => (a.time > b.time ? -1 : a.time < b.time ? 1 : 0));
        return entries;
    }

    function entriesToCsv(entries) {
        const lines = ["time,game,bet,multiplier,profit"];
        for (const e of entries) {
            lines.push(`${e.time},${e.game},${e.bet},${e.multiplier},${e.profit}`);
        }
        return lines.join("\n");
    }

    function downloadFile(content, filename, mimeType) {
        const blob = new Blob([content], { type: mimeType });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        a.style.display = "none";
        document.body.appendChild(a);
        a.click();
        setTimeout(() => {
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }, 100);
    }

    function setButtonState(btn, text, resetLabel, timeout) {
        if (!btn) return;
        btn.textContent = text;
        btn.disabled = true;
        if (resetLabel) {
            setTimeout(() => { btn.textContent = resetLabel; btn.disabled = false; }, timeout || 3000);
        }
    }

    async function exportCsv() {
        const btn = document.getElementById("bet-export-csv-btn");
        setButtonState(btn, "Exporting...", null);

        try {
            const rawEntries = await getBetData();
            if (!rawEntries || rawEntries.length === 0) {
                setButtonState(btn, "No Data Found", "Export CSV", 3000);
                return;
            }
            const entries = parseEntries(rawEntries);
            const csv = entriesToCsv(entries);
            downloadFile(csv, `${currency}_bet_history.csv`, "text/csv;charset=utf-8;");
            console.log(`[BetExport] Exported ${entries.length} entries to CSV`);
            setButtonState(btn, `${entries.length} bets`, "Export CSV", 3000);
        } catch (e) {
            console.error("[BetExport] CSV export failed:", e);
            setButtonState(btn, "Export Failed", "Export CSV", 3000);
        }
    }

    async function exportJson() {
        const btn = document.getElementById("bet-export-json-btn");
        setButtonState(btn, "Exporting...", null);

        try {
            const rawEntries = await getBetData();
            if (!rawEntries || rawEntries.length === 0) {
                setButtonState(btn, "No Data Found", "Export JSON", 3000);
                return;
            }
            const json = JSON.stringify(rawEntries, null, 2);
            downloadFile(json, `${currency}_bet_history.json`, "application/json;charset=utf-8;");
            console.log(`[BetExport] Exported ${rawEntries.length} raw entries to JSON`);
            setButtonState(btn, `${rawEntries.length} entries`, "Export JSON", 3000);
        } catch (e) {
            console.error("[BetExport] JSON export failed:", e);
            setButtonState(btn, "Export Failed", "Export JSON", 3000);
        }
    }

    function makeButton(id, label, color, hoverColor, onClick) {
        const btn = document.createElement("button");
        btn.id = id;
        btn.textContent = label;
        btn.style.cssText = [
            "padding: 8px 16px",
            `background: ${color}`,
            "color: white",
            "border: none",
            "border-radius: 6px",
            "font-size: 13px",
            "font-weight: 600",
            "cursor: pointer",
            "transition: background 0.2s",
        ].join(";");
        btn.addEventListener("mouseenter", () => { btn.style.background = hoverColor; });
        btn.addEventListener("mouseleave", () => { btn.style.background = color; });
        btn.addEventListener("click", onClick);
        return btn;
    }

    // Create floating export buttons
    function createButtons() {
        const container = document.createElement("div");
        container.style.cssText = [
            "position: fixed",
            "bottom: 20px",
            "right: 20px",
            "z-index: 99999",
            "display: flex",
            "gap: 8px",
            "box-shadow: 0 4px 12px rgba(0,0,0,0.3)",
            "border-radius: 8px",
            "padding: 6px",
            "background: rgba(0,0,0,0.6)",
        ].join(";");

        container.appendChild(makeButton("bet-export-csv-btn", "Export CSV", "#2563eb", "#1d4ed8", exportCsv));
        container.appendChild(makeButton("bet-export-json-btn", "Export JSON", "#059669", "#047857", exportJson));

        document.body.appendChild(container);
    }

    // Wait for page to be ready, then add buttons
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", createButtons);
    } else {
        createButtons();
    }
})();
