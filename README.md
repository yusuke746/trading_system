# AI Trading System v4.5

**対象:** XAUUSD / GOLD#（XMTrading）　**時間軸:** 5M足　**手法:** SMC（Smart Money Concepts）+ マルチレジーム　**状態:** デモ稼働中

TradingView Pine Script（5M足）が生成したアラートJSONをWindows VPS上のFlaskで受信し、
ルールベーススコアリングエンジンでエントリー可否を判定、承認されたシグナルをMT5に自動発注するシステム。
LLMは一切使用しない完全ルールベース設計。

---

## 目次

1. [システム構成図](#1-システム構成図)
2. [ファイル構成](#2-ファイル構成)
3. [レジーム分類](#3-レジーム分類)
4. [エントリー条件（Gate1〜3）](#4-エントリー条件gate13)
5. [SMCシグナル検出ロジック](#5-smcシグナル検出ロジックpine-script)
6. [BREAKOUTレジーム検出（2段階）](#6-breakoutレジーム検出2段階)
7. [スコアリングテーブル](#7-スコアリングテーブル)
8. [エグジットロジック](#8-エグジットロジック)
9. [リスク管理](#9-リスク管理)
10. [Discord通知](#10-discord通知)
11. [CSV分析基盤](#11-csv分析基盤)
12. [開発ロードマップ](#12-開発ロードマップ)
13. [既知の問題・TODO](#13-既知の問題todo)
14. [セットアップ手順](#14-セットアップ手順)
15. [テスト実行](#15-テスト実行)
16. [バージョン履歴](#16-バージョン履歴)

---

## 1. システム構成図

```
TradingView (Pine Script 5M / v6)
    │  Webhook JSON (HTTP port 80)
    ▼
Windows VPS / ABLENET (150.66.32.144)
├── python/app.py              # Flask受信サーバー（エントリーポイント）
├── scoring_engine.py          # Gate1〜3 + スコアリング判定
├── executor.py                # MT5発注 + Discord通知
├── position_manager.py        # ポジション管理（BE移動/部分TP/トレーリング）
├── risk_manager.py            # リスク管理（日次損失/連続損失制限/ギャップ）
├── news_filter.py             # ニュースブラックアウト（固定スケジュール）
├── discord_notifier.py        # Discord通知（7イベント）
├── health_monitor.py          # MT5死活監視・自動再接続
├── wait_buffer.py             # waitシグナル再評価バッファ
└── MT5 (XMTrading デモ口座 GOLD#)

Pine Scriptファイル:
├── pine/alert_sender_5m.pine  # メイン（レジーム判定＋アラート送信＋描画統合）
├── pine/csv_exporter.pine     # CSV出力専用（オフライン分析用）
└── pine/regime_detector.pine  # 未使用（参考ファイル）
```

### リクエスト処理フロー

```
Pine Script alert()
    → POST /webhook
    → validate_and_normalize()
    → scoring_engine.py（Gate1 → Gate2 → Gate3 → スコア計算）
        → approve: executor.py → MT5発注 → Discord通知
        → wait:    wait_buffer.py（再評価キューへ登録）
        → reject:  Discord通知のみ（発注なし）
    → scoring_history テーブルに全件記録
```

---

## 2. ファイル構成

```
trading_system/
├── python/
│   ├── app.py                # Flask メインエントリーポイント
│   └── csv_analyzer.py       # TradingViewエクスポートCSV分析ツール
├── pine/
│   ├── alert_sender_5m.pine  # Pine Script v6（5M足/アラート送信/描画統合）
│   ├── csv_exporter.pine     # CSV出力専用インジケーター
│   └── regime_detector.pine  # 未使用（参考ファイル）
├── data/
│   ├── OANDA_XAUUSD_5.csv          # OANDA提供 XAUUSD 5M OHLCVデータ
│   ├── ohlcv_XAUUSD_5m_2y.csv      # yfinance取得 2年分データ
│   ├── ohlcv_XAUUSD_5m_insample.csv  # 学習期間データ（直近6ヶ月）
│   ├── ohlcv_XAUUSD_5m_outsample.csv # 検証期間データ（7〜12ヶ月前）
│   └── split_manifest.json          # データ分割メタ情報
├── tests/
│   ├── test_scoring_engine.py  # スコアリングエンジン（29件）
│   ├── test_risk_manager.py
│   ├── test_executor.py
│   ├── test_ai_judge_v3.py
│   ├── test_backtester.py
│   ├── test_database.py
│   ├── test_db_maintenance.py
│   ├── test_data_structurer.py
│   ├── test_llm_structurer.py
│   ├── test_meta_optimizer.py
│   └── test_news_filter.py
├── docs/
│   └── step2_5_json_design.md  # JSONスキーマ設計書
├── config.py              # 全設定の一元管理（SCORING_CONFIG含む）
├── scoring_engine.py      # ゲートチェック + スコアリングエンジン
├── executor.py            # MT5注文執行 + Discord通知
├── position_manager.py    # BE移動・部分決済・トレーリング管理
├── discord_notifier.py    # Discord Webhook通知
├── risk_manager.py        # 日次損失・連続損失・ギャップリスク管理
├── news_filter.py         # 高インパクトニュースブラックアウト
├── wait_buffer.py         # waitシグナル再評価バッファ
├── revaluator.py          # wait シグナル再評価
├── health_monitor.py      # MT5接続監視・自動再接続
├── market_hours.py        # 市場クローズ判定
├── database.py            # SQLite接続管理
├── logger_module.py       # ログ記録
├── context_builder.py     # MT5市場データ収集
├── loss_analyzer.py       # 含み損分析・アラート
├── ai_judge.py            # パイプライン窓口（後方互換）
├── backtester_live.py     # ライブ型バックテスター（scoring_engineと同一コード）
├── backtester.py          # 簡易バックテスター
├── optimize_exit_params.py # Optunaエグジットパラメータ最適化
├── param_optimizer.py     # パラメータ最適化
├── meta_optimizer.py      # メタ最適化
├── dashboard.py           # ブラウザダッシュボード（/dashboard）
├── notifier.py            # 通知（Discord/LINE）
├── validation.py          # アラートJSON検証・正規化
├── download_ohlcv.py      # yfinance OHLCVデータ取得
├── download_ohlcv_2y.py   # 2年分OHLCVデータ取得
├── requirements.txt
└── start_trading.bat      # 起動バッチ（タスクスケジューラ登録用）
```

---

## 3. レジーム分類

Pine Script が各バーで以下の優先順位でレジームを決定する。
BREAKOUT は 5M足レベルで判定し（後述セクション6参照）、上位レジーム（TREND）が成立している場合は上書きされない。

| 優先順位 | レジーム | 判定条件 | 状態 |
|---|---|---|---|
| 1 | **TREND** | 15M ADX ≥ 25、かつ H1トレンド有効（H1 ADX ≥ 25）、かつ 15M EMA位置がH1方向と一致 | ✅ 稼働中 |
| 2 | **BREAKOUT** | **5M足判定:** Stage1（レンジ認定）AND Stage2（ブレイク認定）、TREND非成立時のみ有効（セクション6参照） | ✅ 稼働中（v4.5データドリブン改修） |
| 3 | ~~REVERSAL~~ | ~~ADX低下中~~ | ❌ 廃止（Gate2でreject） |
| 4 | **RANGE** | 上記すべて非該当 | ❌ Gate2でreject |

### 方向決定ロジック

| レジーム | 方向の決定方法 |
|---|---|
| **TREND** | H1方向が bull かつ 15M close > EMA → buy。H1方向が bear かつ 15M close < EMA → sell |
| **BREAKOUT** | Stage2でclose がボックス上限を超えた → buy。close がボックス下限を下回った → sell |
| ~~REVERSAL~~ | ~~H1方向の逆（逆張り）~~ （廃止） |

---

## 4. エントリー条件（Gate1〜3）

### Gate 1（共通フィルター）

| 条件 | 内容 | 備考 |
|---|---|---|
| `h1_adx >= 25` | H1足ADXでトレンド強度を確認 | **BREAKOUTレジームは免除**（ブレイク直後はADXが遅行するため） |

### Gate 2（レジームフィルター）

| 条件 | 内容 |
|---|---|
| `regime ≠ RANGE` | レンジ相場は即reject |
| `regime ≠ REVERSAL` | 廃止レジームは即reject |
| `direction ≠ none` | 方向未確定は即reject |

### Gate 3（レジーム別SMCフィルター）

#### TREND
以下のSMC条件のいずれか**1つ以上**を満たすこと（OR条件）。
複数条件が揃うほどスコアが高くなりapproveされやすくなる。

| 条件 | シグナル名 |
|---|---|
| CHoCH確認 | `choch_confirmed == true` |
| FVGヒット（方向一致） | `fvg_aligned == true` |
| Zoneヒット（方向一致） | `zone_aligned == true` |
| BOS確認 | `bos_confirmed == true` |
| OBヒット | `ob_aligned == true` |

#### BREAKOUT

| 条件 |
|---|
| `fvg_aligned == true` または `zone_aligned == true`（リテスト確認） |

---

## 5. SMCシグナル検出ロジック（Pine Script）

### CHoCH（Change of Character）

- **定義:** 直近スイングポイントを更新した時点で構造転換と判定
- **上昇CHoCH:** `close > last_swing_high AND close[1] <= last_swing_high`
- **下降CHoCH:** `close < last_swing_low AND close[1] >= last_swing_low`
- チャート上にラベル（CHoCH↑ / CHoCH↓）を表示

### BOS（Break of Structure）

**v4.3バグ修正済み（CHoCHリセット前にBOS評価）。**

| バージョン | 処理順序 | 問題 |
|---|---|---|
| 修正前 | CHoCH判定 → CHoCHリセット → BOS評価 | CHoCHがlast_swing_highをnaにリセットした後にBOSを評価するためnot na()チェックで常にfalse |
| **修正後** | CHoCH判定 → **BOS評価** → CHoCHリセット | BOS評価をCHoCHリセットより前に実施することで正常発火 |

- **BOS Pending管理:** BOSバーから `bos_timeout_bars`（デフォルト20本=100分）以内にプルバックがなければキャンセル
- `bos_confirmed` = `bos_pending_bull or bos_pending_bear`（Pending中を示すフラグ）
- チャート上にラベル（BOS↑ / BOS↓）を表示

### OB（Order Block）

- **Bullish OB:** BOS上昇確認時の1本前の陰線キャンドル本体
- **Bearish OB:** BOS下降確認時の1本前の陽線キャンドル本体
- `ob_aligned`: bos_pending中 かつ 方向一致 かつ closeがOB内にある

### FVG（Fair Value Gap）

- **Bull FVG:** `high[2] < low[0]` かつサイズ ≥ `ATR × fvg_atr_min_mult`（デフォルト0.3）
- **Bear FVG:** `low[2] > high[0]`
- 50%以上埋まった場合（closeがFVG中値に到達）または `zone_max_age`（200バー）経過で無効化
- ⚠️ **既知の問題:** 同一FVGへの再タッチで再発火する（識別子未実装）

### Zone（需給ゾーン）

- **Demand Zone（買い需要）:** 陰線後に上昇インパルス（`ATR × zone_impulse_mult`以上）が来た場合に確定
- **Supply Zone（売り供給）:** 陽線後に下落インパルスが来た場合に確定
- `zone_impulse_bars`（デフォルト6バー）以内にインパルスが来なければキャンセル
- ⚠️ **既知の問題:** FVGと同様に再使用制御なし

### Sweep（流動性スイープ）

- **安値スイープ:** 直近安値をヒゲが貫通し、closeが安値より上に戻る（buy方向シグナル）
- **高値スイープ:** 直近高値をヒゲが貫通し、closeが高値より下に戻る（sell方向シグナル）
- 貫通量 ≥ `ATR × sweep_atr_mult`（デフォルト0.2）を条件とする

---

## 6. BREAKOUTレジーム検出（2段階）

### 背景（v4.5 データドリブン改修）

**分析期間:** 2025-12-07〜2026-03-27（101日・21,280バー）

旧BREAKOUTロジック（ATR > ATR_MA20×1.5 AND ADX急上昇）では60件が検出されたが、
**98%以上が誤検知**（トレンド継続局面をブレイクアウトと誤認）と判明。
CSVデータ分析に基づきStage1/Stage2の2段階判定に改修した。

**推奨パラメータ（5件/日の目標に対し4.6件/日を達成）:**

| パラメータ | input変数 | デフォルト値 |
|---|---|---|
| レンジ認定期間 | `bo_lookback_bars` | 24バー |
| ボックス最大ATR倍率 | `bo_box_atr_max` | 5.0倍 |
| レンジADX上限 | `bo_adx_avg_max` | 25.0 |
| ブレイク最小ATR倍率 | `bo_break_atr_min` | 0.2倍 |

### Stage1: レンジ認定

```pine
bo_box_high      = ta.highest(high[1], bo_lookback_bars)   // 過去N本の高値
bo_box_low       = ta.lowest(low[1],   bo_lookback_bars)   // 過去N本の安値
bo_box_width     = bo_box_high - bo_box_low
bo_box_atr_ratio = atr5 > 0 ? bo_box_width / atr5 : 999.0 // ボックス幅/ATR倍率
bo_adx_avg       = ta.sma(m15_adx, bo_lookback_bars)       // ADX平均

stage1_range = bo_box_atr_ratio < bo_box_atr_max and bo_adx_avg < bo_adx_avg_max
```

### Stage2: ブレイク認定

```pine
bo_break_up   = close > bo_box_high and (close - bo_box_high) / atr5 > bo_break_atr_min
bo_break_down = close < bo_box_low  and (bo_box_low - close) / atr5 > bo_break_atr_min

stage2_break = bo_break_up or bo_break_down
```

### 最終判定

```pine
is_breakout = stage1_range and stage2_break

// TREND優先（TRENDレジームが成立している場合はBREAKOUTに上書きしない）
regime    := is_breakout and regime != "TREND" ? "BREAKOUT" : regime
direction := regime == "BREAKOUT" ? (bo_break_up ? "buy" : "sell") : direction
```

---

## 7. スコアリングテーブル

### 判定フロー

```
Gate1（h1_adx確認、BREAKOUTは免除）
    ↓ 通過
Gate2（レジーム確認）
    ↓ 通過
Gate3（SMC条件確認）
    ↓ 通過
スコア計算（全項目を加算）
    ↓
合計スコア ≥ 0.50 → approve（発注）
合計スコア ≥ 0.00 → wait（再評価キュー）
合計スコア < 0.00 → reject（不発注）
```

### スコアテーブル（2026-03-21 CSVデータ分析に基づいて全面再設計）

| 条件 | キー | 加減点 | 根拠 |
|---|---|---|---|
| BOS確認 | `bos_confirmed` | **+0.30** | CSV分析: PF=1.22（687件）、最高信頼性 |
| BOS+Sweep同時 | `bos_and_sweep` | **+0.20** | CSV分析: PF=1.48（76件）ボーナス |
| OBヒット | `ob_aligned` | **+0.20** | BOSの完成形（リテスト到達） |
| OB+FVGボーナス | `ob_and_fvg` | **+0.10** | OBとFVGの複合シグナル |
| FVG+Zone同時 | `fvg_and_zone_overlap` | **+0.30** | CSV分析: PF=4.81（17件）、最高精度 |
| CHoCH確認 | `choch_strong` | **+0.10** | 単独効果は限定的（旧+0.20から引き下げ） |
| FVG単独ペナルティ | `fvg_only_penalty` | **-0.20** | CSV分析: PF=0.55（負け） / bos・ob・zoneが全て0の場合のみ適用 |
| Zone単独ペナルティ | `zone_only_penalty` | **-0.20** | CSV分析: PF=0.86（負け） / bos・ob・fvgが全て0の場合のみ適用 |
| Tokyoセッション | `session_tokyo` | **+0.10** | CSV分析: PF=1.14（旧-0.10から逆転） |
| NYセッション | `session_ny` | **+0.05** | CSV分析: PF=1.07（旧±0.00から改善） |
| Offアワーズ | `session_off` | **-0.05** | CSV分析: PF=0.93（旧-0.20から緩和） |
| London/NYオーバーラップ | `session_london_ny` | **-0.10** | CSV分析: PF=0.89（旧+0.10から逆転） |
| Londonセッション | `session_london` | **-0.15** | CSV分析: PF=0.83（旧+0.05から逆転） |
| 15M ADX 25〜35 | `adx_normal` | **+0.10** | 健全なトレンド強度範囲 |
| H1方向一致 | `h1_direction_aligned` | **+0.10** | HTFとの方向一致で精度向上 |
| ATR ratio 0.8〜1.5 | `atr_ratio_normal` | **+0.05** | 正常ボラティリティ範囲 |
| ATR ratio > 1.5 | `atr_ratio_high` | **-0.05** | ボラティリティ過熱 |
| 高インパクトニュース前後 | `news_nearby` | **-0.30** | 最大ペナルティ（スプレッド拡大・急変リスク） |
| RSIダイバージェンス | `rsi_divergence` | **0.00** | 未実装のため無効化（TODO: Phase 5） |

### approve_threshold = 0.50 の設計根拠

閾値シミュレーション結果（2026-03-22 CSVシミュレーション、59日間データ）:

- 閾値0.40: 1日9.49件 PF=1.00（トントン・スプレッド込みで実質赤字）
- 閾値0.50: 1日6.24件 PF=1.17（目標5件/日クリア・黒字）

典型的なapproveパターン:

```
bos単独(+0.30) + tokyo(+0.10) + h1_aligned(+0.10) = 0.50 → approve ✅
bos単独(+0.30) + tokyo(+0.10) + adx(+0.10)        = 0.50 → approve ✅
bos単独(+0.30) + london(-0.15)                    = 0.15 → reject ❌
fvg+zone(+0.30) + bos(+0.30)                      = 0.60 → approve ✅
bos+sweep(+0.50)                                  = 0.50 → approve ✅
```

---

## 8. エグジットロジック

### TRENDレジーム（3ステップ管理）

Optunaで最適化したパラメータ（P2, 2026-03-12）を使用。

| ステップ | トリガー | 内容 |
|---|---|---|
| 1. ブレークイーブン(BE) | 含み益 = ATR×**1.8** | SLをエントリー価格 + ATR×0.15バッファへ移動 |
| 2. 部分決済(50%) | 含み益 = ATR×**3.6** | ポジションの50%を利確 |
| 3. トレーリングストップ | 部分決済後に自動起動 | 高値/安値更新ごとにATR×**1.2**で追随 |
| 最終TP | ATR×**6.0** | 残り50%全決済（実際にはトレーリングSLで先に決済されることが多い） |

### BREAKOUTレジーム（固定SL/TPのみ）

BEと部分決済・トレーリングは行わない。

| パラメータ | 値 |
|---|---|
| SL | ATR × 2.7 |
| TP | ATR × 6.0 |

> ⚠️ **既知の問題:** BREAKOUTのTP×6.0は5M足では非現実的に長い。Phase 3でTP縮小＋BE追加を検討中。

### SL共通設定

```
SL距離 = ATR × 2.7
制約: min 8ドル / max 80ドル
```

### セッション別 SL/TP 乗数補正

| セッション | SL乗数 | TP乗数 | 設計理由 |
|---|---|---|---|
| Asia / Off | ×0.75 | ×0.75 | 低ボラ・レンジ → SL/TPを絞り費用対効果を改善 |
| London / NY | ×1.00 | ×1.00 | 通常（基準値） |
| London_NY | ×1.30 | ×1.30 | 最高ボラ → SL/TPを広げノイズ耐性と利幅を確保 |

---

## 9. リスク管理

4重の保護機構を実装している。

| 保護レイヤー | 内容 | 設定値 |
|---|---|---|
| 1. 日次損失制限 | 残高の-5%超過で自動停止（当日の全発注をブロック） | -5% |
| 2. 連続損失制限 | 3連敗（連続SL被弾）で自動停止 | 3連敗 |
| 3. 最大同時ポジション | 同時保有ポジション数の上限 | 5ポジション |
| 4. 合計リスク上限 | 全ポジションの合計リスクを残高10%以内に制限 | 10% |

### demo_mode フラグ（デモ稼働中のデータ収集用）

`config.py` の `SYSTEM_CONFIG["demo_mode"]` で、日次損失制限（レイヤー1）と
連続損失制限（レイヤー2）を一括で無効化できる。
最大ポジション数・合計リスク上限・ギャップチェックは引き続き有効。

| `demo_mode` | 日次損失制限 | 連続損失制限 |
|---|---|---|
| `True`（現在） | **無効**（スキップ） | **無効**（スキップ） |
| `False` | 有効（-5%で停止） | 有効（3連敗で停止） |

> ⚠️ **リアル口座移行前に必ず `demo_mode: False` に変更すること。**

### ニュースブラックアウト（固定スケジュール）

`HIGH_IMPACT_UTC_TIMES`（config.py）で設定された固定時間帯は発注をブロック。

| 曜日 | 時間(UTC) | イベント |
|---|---|---|
| 金曜 | 12:00〜14:00 | NFP（雇用統計） |
| 水曜 | 18:00〜20:00 | FOMC（FRB金融政策） |

### その他ブロック条件

| 条件 | 設定値 |
|---|---|
| デイリーブレイク | 23:45〜1:00 UTC（XMサーバータイム） |
| 指値注文キャンセル開始 | 23:30 UTC |
| 週明けギャップブロック | 月曜早朝の価格ギャップが $15 超 |

---

## 10. Discord通知

`.env` に `DISCORD_WEBHOOK_URL` を設定すると以下の8イベントで通知が送信される。
未設定時は通知をスキップする（エラーにはならない）。

| イベント | タイトル | 色 |
|---|---|---|
| Webhook受信（スコア判定前） | 📡 シグナル受信 | グレー |
| スコアリング承認 | ✅ エントリー承認 | 緑 |
| スコアリング否決 | ❌ エントリー否決 | 赤 |
| 発注成功 | 🚀 発注成功 | 緑 |
| 発注失敗 | ⚠️ 発注失敗 | 黄 |
| ポジション決済（利確） | 💰 決済（利確） | 緑 |
| ポジション決済（損切） | 🔴 決済（損切） | 赤 |
| MT5接続断 | 🆘 システムアラート | 赤 |

---

## 11. CSV分析基盤

### 背景

スコアリングテーブルの値は設計者の経験則で決めていたが、実際のシグナルデータで
検証したところ**セッション評価が現実と真逆**であることが判明した。
この問題を解決するためCSV分析基盤を構築した。

### csv_exporter.pine

TradingView Proの「チャートデータをエクスポート」機能用に作成した独立ペインの
インジケーター。`plot()` で全条件を数値エンコードして出力し、過去データを一括CSVとして
取得できる（`alert()` = リアルタイムのみ、の制限を回避）。

**数値エンコード仕様:**

| フィールド | エンコード |
|---|---|
| regime | TREND=1, BREAKOUT=2, RANGE=3, REVERSAL=4 |
| direction | buy=1, sell=-1, none=0 |
| h1_direction | bull=1, bear=-1 |
| session | london_ny=5, london=4, ny=3, tokyo=2, off=1 |
| choch, fvg, zone, bos, ob, sweep, alert_fired | true=1, false=0 |
| h1_adx, m15_adx, atr, atr_ratio | 実数値そのまま |

### csv_analyzer.py

TradingViewエクスポートCSVを読み込み、各条件組み合わせの勝率・PF・期待値を
計算するオフライン分析ツール。

```bash
py python/csv_analyzer.py data/export.csv
```

**分析レポート項目:**
- 全体サマリー（勝率 / PF / 期待値 / 総件数）
- レジーム別・セッション別・SMC条件組み合わせ別成績（上位15位）
- H1方向一致/不一致の比較
- スコア閾値シミュレーション（件数/日 × PFのトレードオフ）
- 月別成績・連勝連敗分析・ドローダウン分析・方向別成績

### 分析実績（2025-12-07〜2026-03-21、59日間）

スコアリング全面再設計の根拠データ。

**SMC条件組み合わせ別 実績（approve_threshold=0.50適用後）:**

| 条件組み合わせ | PF | 期待値 | 件数 |
|---|---|---|---|
| **fvg+zone同時** | **4.81** | **+2.42R** | 17件 |
| bos+sweep | 1.48 | +0.65R | 76件 |
| bos単独 | 1.22 | +0.32R | 687件 |
| choch+bos | 1.13 | +0.20R | 111件 |
| zone単独 | 0.86 | -0.25R | 183件 |
| fvg単独 | 0.55 | -0.88R | 118件 |

**セッション別 実績:**

| セッション | 実績PF | 旧スコア | 新スコア | 変更理由 |
|---|---|---|---|---|
| tokyo | 1.14 | -0.10 | **+0.10** | 実績優良→逆転 |
| ny | 1.07 | ±0.00 | **+0.05** | 実績良好→加点 |
| off | 0.93 | -0.20 | **-0.05** | 実績は軽微な負け→緩和 |
| london_ny | 0.89 | +0.10 | **-0.10** | 実績不振→逆転 |
| london | 0.83 | +0.05 | **-0.15** | 実績最悪→逆転 |

**閾値0.50適用後サマリー（2025-12-07〜2026-03-22）:**

| 指標 | 値 |
|---|---|
| 分析期間 | 2025-12-07〜2026-03-20（59日・20,043バー） |
| 総エントリー（閾値0.50） | 630件 / 6.4件/日 |
| 勝率 | 44.1% |
| PF | 1.17 |
| 期待値 | +0.26R/トレード |
| 最大ドローダウン | -201.0R |
| 平均保有時間 | 約2.3時間（27.3バー） |

> ⚠️ 最大連敗48回・最大DD-201.0Rへの資金管理設計が必要。リアル運用時は `risk_percent` を0.10〜0.20%程度に下げること推奨。

---

## 12. 開発ロードマップ

| フェーズ | 内容 | 状態 |
|---|---|---|
| Phase 0-1 | `csv_exporter.pine` 作成（全バーCSV出力用インジ） | ✅ 完了 |
| Phase 0-2 | `csv_analyzer.py` 作成・分析実行・詳細レポート追加 | ✅ 完了 |
| Phase 1-1 | BOS/OBバグ修正（CHoCHリセット前にBOS評価する処理順序修正） | ✅ 完了 |
| Phase 2   | スコアリング全面再設計（CSVデータ根拠）・閾値0.50に変更 | ✅ 完了 |
| Phase 2.5 | BREAKOUTレジーム判定をデータドリブン2段階方式に改修（v4.5） | ✅ 完了 |
| Phase 1-2 | ニュースフィルター動的実装（経済指標カレンダーAPI連携） | 🔲 未着手 |
| Phase 1-3 | FVG/Zone再使用制御＋重複エントリーブロック実装 | 🔲 未着手 |
| Phase 3   | BREAKOUTエグジット再設計（TP縮小＋BE追加） | 🔲 未着手 |
| Phase 4-1 | FVG/Zoneを15M足ベースに変更（5M足のノイズ対策） | 🔲 検討中 |
| Phase 4-2 | ピラミッディング（FVG識別子実装後に実施） | 🔲 未着手 |
| Phase 5   | RSIダイバージェンス実装（スコア0.00の項目を有効化） | 🔲 未着手 |

---

## 13. 既知の問題・TODO

優先度順に記載する。

| 優先度 | 項目 | 詳細 |
|---|---|---|
| 🔴 高 | FVG/Zone再使用制御なし | 同一FVG/Zoneへの再タッチのたびに再発火する。識別子（bar_index等）で1回のみ発火に変更が必要 |
| 🔴 高 | 重複エントリーブロック未実装 | 同方向ポジションが既存の場合でも新規エントリーする。max_positionsでの上限管理のみ |
| 🟡 中 | `news_nearby` 動的実装なし | NFP/FOCMの固定ブラックアウトのみ。他の重要経済指標・BOJ・ECBは非対応 |
| 🟡 中 | `rsi_divergence` 未実装 | Pine Scriptで常にfalse固定。スコアテーブルに0.00で登録済みだが機能していない |
| 🟡 中 | BREAKOUTのTP×6.0 | 5M足では非現実的に長いTP設定。Phase 3で縮小＋BE追加を予定 |
| 🟢 低 | 5M足FVG/Zoneのノイズ問題 | 5M足は細かすぎてノイズが多い。15M足への変更を検討中 |
| 🟢 低 | タスクスケジューラ自動起動 | VPS再起動後の自動起動。`start_trading.bat` は作成済みだが登録確認が必要 |

---

## 14. セットアップ手順

### 必要環境

| 項目 | 要件 |
|---|---|
| OS | Windows 10/11 または Windows Server 2022 |
| Python | 3.10以上 |
| MetaTrader 5 | XMTrading インストール・デモ口座ログイン済み |
| TradingView | Pro以上（Webhookアラート機能が必要） |

### インストール

```bash
git clone <repository_url>
cd trading_system
pip install -r requirements.txt
```

### 環境変数設定

```env
# .env（.env.example からコピーして設定）
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...  # オプション
```

### 起動方法

```bat
rem Windows VPS での起動
set PYTHONPATH=C:\Users\Administrator\trading_system
cd C:\Users\Administrator\trading_system
git pull
py python/app.py
```

```bash
# 起動確認
curl http://localhost:80/health
```

### TradingViewアラート設定

1. GOLD# 5Mチャートに `pine/alert_sender_5m.pine` を追加
2. アラートを作成: 条件 = "AI Alert Sender [XAUUSD/5M]" → alert()関数の呼び出し
3. Webhook URL = `http://150.66.32.144/webhook`
4. メッセージ欄は空白（Pine Script内で `alert(json)` を直接送信するため不要）

### CSV分析（オフライン分析用）

1. TradingView Proで `pine/csv_exporter.pine` を追加
2. 5Mチャートで「チャートデータをエクスポート」→ CSVを `data/` に保存
3. 分析実行: `py python/csv_analyzer.py data/export.csv`

---

## 15. テスト実行

```bash
cd trading_system
py -m pytest tests/ -v
```

| ファイル | テスト数 | 対象 |
|---|---|---|
| `test_scoring_engine.py` | **29件** | Gate1〜3判定・スコア計算・閾値判定・ペナルティ関数 |
| `test_risk_manager.py` | 複数件 | 日次損失制限・連続損失・ギャップフィルター |
| `test_executor.py` | 複数件 | MT5発注・Discordエラーハンドリング |
| `test_ai_judge_v3.py` | 複数件 | 判定パイプライン全体 |
| `test_backtester.py` | 複数件 | バックテストエンジン |
| `test_database.py` | 複数件 | SQLite CRUD操作 |
| `test_db_maintenance.py` | 複数件 | DB定期メンテナンス |
| `test_data_structurer.py` | 複数件 | データ構造化処理 |
| `test_llm_structurer.py` | 複数件 | LLM構造化（後方互換） |
| `test_meta_optimizer.py` | 複数件 | メタ最適化 |
| `test_news_filter.py` | 複数件 | ニュースフィルター・ブラックアウト判定 |

**現在の状態:** `test_scoring_engine.py` 全29件 PASSED（2026-03-22確認済み）

---

## 16. バージョン履歴

| バージョン | 日付 | 主な変更点 |
|---|---|---|
| **v4.5** | 2026-03-27 | BREAKOUTレジーム判定をデータドリブン2段階方式に改修（101日21280バーのCSV分析に基づく）・未使用input（`atr_breakout_mult`・`adx_range_max`）を両pineファイルから削除・README全面更新 |
| **v4.4** | 2026-03-22 | CSVデータ分析基盤構築（csv_exporter.pine・csv_analyzer.py）・BOS/OBバグ修正・スコアリング全面再設計（セッション逆転修正・fvg_only_penalty・zone_only_penalty・bos_and_sweep追加）・approve_threshold 0.15→0.50・demo_mode実装 |
| **v4.3** | 2026-03 | BOS/OBバグ修正（CHoCHリセット前にBOS評価）・scoring_history全件記録・BOJ固定ブラックアウト削除・FOMC時間修正（21-24→18-20）・損失アラート通知削除 |
| **v4.2** | 2026-02 | REVERSAL廃止（Gate2でreject）・Pine Script発火条件OR化・approve閾値0.15に引き下げ・描画統合（FVG/Zone/OB/BOS/CHoCHラベル）・シンボル変換（XAUUSD→GOLD#）・JPY口座USD換算バグ修正 |
| **v4.1** | 2026-01 | should_alert条件をレジーム+direction確定のみに緩和（Gate3重複解消）・Discord通知実装（7イベント）・Windows VPS（ABLENET）移行完了 |
| **v4.0** | 2025-12 | Pine Script→Flask直接送信アーキテクチャに刷新・LLM廃止・マルチレジーム対応・BOS/OB実装・Optuna最適化 |
| v3.5 | 2025-11 | `structurize()` をルールベースデフォルト化・MetaOptimizer追加 |
| v3.0 | 2025-10 | LLMを構造化専任に変更・数値ルールベーススコアリング導入・ライブ型バックテスター追加 |
| v2.2 | 2025-09 | SQLite接続プール化・ニュースフィルターフェイルセーフ・日次損失上限-5% |
| v2.1 | 2025-08 | ユニットテスト追加・backtester.py・param_optimizer.py |
| v2.0 | 2025-07 | LLM判定廃止→数値ルール化 |
| v1.x | 2025-06 | 初期実装（LLMベース判定・Forex対応） |
