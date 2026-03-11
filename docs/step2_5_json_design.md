# STEP2.5: アラートJSONフィールド設計

## 設計方針
- Pine Script側：条件の成立フラグ（生データ）を出力するのみ
- Python側：スコアリング計算・approve/wait/reject判定
- フィールドは過不足なく、Pythonのスコアテーブルに対応させる

---

## JSONフィールド一覧

```json
{
  // ─── 基本情報 ───────────────────────────────────────────
  "symbol":     "XAUUSD",
  "timeframe":  "15",
  "timestamp":  "2026-03-10T12:30:00Z",
  "price":      5214.22,
  "atr":        11.18,

  // ─── レジーム層 ──────────────────────────────────────────
  "regime":     "TREND",        // "TREND" | "REVERSAL" | "BREAKOUT" | "RANGE"
  "direction":  "buy",          // "buy" | "sell" | "none"
  "h1_direction": "bull",       // "bull" | "bear"
  "h1_adx":     28.3,           // 1H ADX値
  "m15_adx":    31.2,           // 15M ADX値
  "m15_adx_drop": 2.1,          // ADX低下幅（REVERSAL判定用）
  "atr_ratio":  1.3,            // 現在ATR ÷ ATR MA20（BREAKOUT判定用）

  // ─── SMC構造フラグ ───────────────────────────────────────
  // CHoCH
  "choch_confirmed": true,      // 方向一致のCHoCHが確認されたか
                                // buy方向ならbullish_choch、sell方向ならbearish_choch

  // FVG
  "fvg_hit":    true,           // 現在価格がFVG内にいるか
  "fvg_aligned": true,          // FVGの方向がdirectionと一致しているか

  // Zone
  "zone_hit":   true,           // 現在価格がZone内にいるか
  "zone_aligned": true,         // Zoneの方向（demand/supply）がdirectionと一致しているか

  // ─── スコアリング用フラグ（Python側で加点） ──────────────
  "rsi_divergence": false,      // RSIダイバージェンス検出
                                // REVERSAL用・スコア加点のみ（ゲートに入れない）

  "sweep_detected": false,      // 流動性スイープ検出（REVERSAL用スコア加点）
                                // 定義：直前swing_length×2本のスイング高値/安値を
                                //       ヒゲが超えかつcloseが戻ったバー
                                //       （ヒゲ貫通量 >= ATR×0.2 を条件とする）
                                // ※STEP3で新規実装

  "candle_pattern": "none",     // "pin_bar" | "engulfing" | "none"
                                // REVERSAL用スコア加点

  "session": "london_ny",       // "london_ny" | "london" | "ny" | "tokyo" | "off"
                                // セッション判定（UTC基準）
                                // london_ny: 13:00-17:00 UTC（ロンドン・NYオーバーラップ）
                                // london:    08:00-13:00 UTC
                                // ny:        17:00-21:00 UTC
                                // tokyo:     00:00-06:00 UTC
                                // off:       それ以外
                                // ※app.pyのsession_score()と一致させること

  "rsi_trend_aligned": true,    // RSI方向確認
                                // buy方向: RSI > 50 → true
                                // sell方向: RSI < 50 → true
  "rsi_value":  54.3,           // 15M RSI生値（Python側での拡張判定用）

  "news_nearby": false          // 高インパクトニュース前後30分以内
                                // ※Pine Script単体では実装困難
                                // → 当面はfalse固定、将来Python側で上書き
}
```

---

## フィールドの使われ方（Python側スコアテーブルとの対応）

### TREND用
| フィールド | 使用目的 |
|---|---|
| m15_adx | ADX 25〜35 +0.10 / ADX 35超 +0.05 |
| h1_adx, m15_adx | 1H・15M ADX方向一致 +0.10 |
| fvg_hit + zone_hit | FVG+Zone両方ヒット +0.15 |
| rsi_trend_aligned | RSI方向確認 +0.10 |
| session | セッション加減点 |
| atr_ratio | ATR倍率による加減点 |
| news_nearby | 高インパクトニュース -0.30 |

### REVERSAL用
| フィールド | 使用目的 |
|---|---|
| m15_adx_drop | ADX低下幅で加点分岐 |
| sweep_detected + zone_hit | スイープ+Zone重複 +0.15 |
| sweep_detected + fvg_hit | スイープ+FVG重複 +0.15 |
| rsi_divergence | RSIダイバ +0.15 |
| candle_pattern | ピンバー/包み足 +0.10 |
| atr_ratio | ATR倍率 +0.05 |
| session, news_nearby | 同上 |

### BREAKOUT用
| フィールド | 使用目的 |
|---|---|
| h1_direction + direction | 1H方向一致 +0.20 |
| atr_ratio | ATR倍率2.0倍超 +0.10 |
| zone_hit + fvg_hit | Zone+FVGリテスト +0.15 |
| rsi_trend_aligned | RSI方向確認 +0.10 |
| session, news_nearby | 同上 |

---

## 確定事項

```
rsi_trend_aligned: buy→RSI>50、sell→RSI<50 Pine側で計算
rsi_value:         15M RSI生値を渡す（Python側拡張用）
sweep_detected:    直前swing_length×2本のスイング高安値を
                   ヒゲが超えかつcloseが戻ったバー
                   貫通量 >= ATR×0.2 を条件とする
                   ※STEP3で新規実装
news_nearby:       当面false固定
                   ⚠️ バックログ：最初の1ヶ月以内に実装すること
                   理由：-0.30は全テーブル最大マイナス項目
                         false固定=この防御が完全に無効な状態
```

---

## 最終JSONフィールド数：24フィールド

| カテゴリ | フィールド数 |
|---|---|
| 基本情報 | 5 |
| レジーム層 | 7 |
| SMC構造フラグ | 5 |
| スコアリング用 | 7 |
| **合計** | **24** |

---

## Pine Script側の実装負荷

```
計算が必要な新規フィールド（STEP1/2に未実装）：
  sweep_detected     → 直前swing_length×2本スイングのヒゲ超えclose戻り（新規）
  candle_pattern     → ピンバー/包み足判定（新規）
  session            → UTC時刻でのセッション分類（新規）
  rsi_trend_aligned  → RSI > 50 / < 50（新規）
  rsi_value          → 15M RSI生値（新規）
  news_nearby        → 当面false固定

それ以外はSTEP1/2の変数を流用できる
```
