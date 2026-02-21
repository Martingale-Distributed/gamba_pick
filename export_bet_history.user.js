// ==UserScript==
// @name         Pick Sites - Export Bet History
// @namespace    https://github.com/lothrop/gamba_pick
// @version      1.0
// @description  Export bet history from *pick sites to CSV via localforage/IndexedDB
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

    function downloadCsv(csv, filename) {
        const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
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

    async function exportHistory() {
        const btn = document.getElementById("bet-export-btn");
        if (btn) {
            btn.textContent = "Exporting...";
            btn.disabled = true;
        }

        try {
            const rawEntries = await getBetData();
            if (!rawEntries || rawEntries.length === 0) {
                const msg = "No bet history found in browser storage for this site.";
                console.warn("[BetExport]", msg);
                if (btn) btn.textContent = "No Data Found";
                setTimeout(() => { if (btn) { btn.textContent = "Export History"; btn.disabled = false; } }, 3000);
                return;
            }

            const entries = parseEntries(rawEntries);
            const csv = entriesToCsv(entries);
            const filename = `${currency}_bet_history.csv`;
            downloadCsv(csv, filename);

            console.log(`[BetExport] Exported ${entries.length} entries to ${filename}`);
            if (btn) btn.textContent = `Exported ${entries.length} bets`;
            setTimeout(() => { if (btn) { btn.textContent = "Export History"; btn.disabled = false; } }, 3000);
        } catch (e) {
            console.error("[BetExport] Export failed:", e);
            if (btn) btn.textContent = "Export Failed";
            setTimeout(() => { if (btn) { btn.textContent = "Export History"; btn.disabled = false; } }, 3000);
        }
    }

    // Create floating export button
    function createButton() {
        const btn = document.createElement("button");
        btn.id = "bet-export-btn";
        btn.textContent = "Export History";
        btn.style.cssText = [
            "position: fixed",
            "bottom: 20px",
            "right: 20px",
            "z-index: 99999",
            "padding: 10px 20px",
            "background: #2563eb",
            "color: white",
            "border: none",
            "border-radius: 8px",
            "font-size: 14px",
            "font-weight: 600",
            "cursor: pointer",
            "box-shadow: 0 4px 12px rgba(0,0,0,0.3)",
            "transition: background 0.2s",
        ].join(";");

        btn.addEventListener("mouseenter", () => { btn.style.background = "#1d4ed8"; });
        btn.addEventListener("mouseleave", () => { btn.style.background = "#2563eb"; });
        btn.addEventListener("click", exportHistory);

        document.body.appendChild(btn);
    }

    // Wait for page to be ready, then add button
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", createButton);
    } else {
        createButton();
    }
})();
