# AI Trading System v4.3

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
6. [スコアリングテーブル](#6-スコアリングテーブル)
7. [エグジットロジック](#7-エグジットロジック)
8. [リスク管理](#8-リスク管理)
9. [Discord通知](#9-discord通知)
10. [CSV分析基盤](#10-csv分析基盤2026-03-21-新規追加)
11. [開発ロードマップ](#11-開発ロードマップ)
12. [既知の問題・TODO](#12-既知の問題todo)
13. [セットアップ手順](#13-セットアップ手順)
14. [テスト実行](#14-テスト実行)
15. [バージョン履歴](#15-バージョン履歴)

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
├── signal_collector.py        # Webhook受信バッファ（500ms窓）
└── MT5 (XMTrading デモ口座 GOLD#)

Pine Scriptファイル:
├── pine/alert_sender_5m.pine  # メイン（レジーム判定＋アラート送信＋描画統合）
├── pine/regime_detector.pine  # レジーム判定（サブインジ）
└── pine/csv_exporter.pine     # CSV出力専用（オフライン分析用）
```

### リクエスト処理フロー

```
Pine Script alert()
    → POST /webhook
    → signal_collector.py（500ms バッファ）
    → batch_processor.py
    → scoring_engine.py（Gate1 → Gate2 → Gate3 → スコア計算）
        → approve: executor.py → MT5発注 → Discord通知
        → wait:    revaluator.py（再評価キューへ登録）
        → reject:  Discord通知のみ（発注なし）
    → scoring_history テーブルに全件記録
```

---

## 2. ファイル構成

```
trading_system/
├── python/
│   ├── app.py                # Flask メインエントリーポイント
│   └── csv_analyzer.py       # TradingViewエクスポートCSV分析ツール（2026-03-21追加）
├── pine/
│   ├── alert_sender_5m.pine  # Pine Script v6（5M足/アラート送信/描画統合）
│   ├── regime_detector.pine  # レジーム判定サブインジケーター
│   └── csv_exporter.pine     # CSV出力専用インジケーター（2026-03-21追加）
├── data/
│   ├── OANDA_XAUUSD_5.csv          # OANDA提供 XAUUSD 5M OHLCVデータ
│   ├── ohlcv_XAUUSD_5m_2y.csv      # yfinance取得 2年分データ
│   ├── ohlcv_XAUUSD_5m_insample.csv  # 学習期間データ（直近6ヶ月）
│   ├── ohlcv_XAUUSD_5m_outsample.csv # 検証期間データ（7〜12ヶ月前）
│   └── split_manifest.json          # データ分割メタ情報
├── tests/
│   ├── test_scoring_engine.py  # スコアリングエンジン（29件）
│   ├── test_risk_manager.py    # リスク管理
│   ├── test_executor.py        # 発注処理
│   ├── test_ai_judge_v3.py     # 判定パイプライン
│   ├── test_backtester.py      # バックテスター
│   ├── test_database.py        # DB接続
│   ├── test_db_maintenance.py  # DBメンテナンス
│   ├── test_data_structurer.py # データ構造化
│   ├── test_llm_structurer.py  # LLM構造化
│   ├── test_meta_optimizer.py  # メタ最適化
│   └── test_news_filter.py     # ニュースフィルター
├── docs/
│   └── step2_5_json_design.md  # JSONスキーマ設計書
├── config.py              # 全設定の一元管理（SCORING_CONFIG含む）
├── scoring_engine.py      # ゲートチェック + スコアリングエンジン
├── executor.py            # MT5注文執行 + Discord通知
├── position_manager.py    # BE移動・部分決済・トレーリング管理
├── discord_notifier.py    # Discord Webhook通知
├── risk_manager.py        # 日次損失・連続損失・ギャップリスク管理
├── news_filter.py         # 高インパクトニュースブラックアウト
├── signal_collector.py    # Webhookバッファ（500ms窓）
├── batch_processor.py     # バッチ処理パイプライン
├── revaluator.py          # wait シグナル再評価
├── health_monitor.py      # MT5接続監視・自動再接続
├── market_hours.py        # 市場クローズ判定
├── database.py            # SQLite接続管理
├── logger_module.py       # ログ記録
├── context_builder.py     # MT5市場データ収集
├── ai_judge.py            # パイプライン窓口（後方互換）
├── backtester_live.py     # ライブ型バックテスター（scoring_engineと同一コード）
├── backtester.py          # 簡易バックテスター
├── optimize_exit_params.py # Optunaエグジットパラメータ最適化
├── param_optimizer.py     # パラメータ最適化
├── meta_optimizer.py      # メタ最適化
├── dashboard.py           # ブラウザダッシュボード
├── notifier.py            # 通知（Discord/LINE）
├── download_ohlcv.py      # yfinance OHLCVデータ取得
├── download_ohlcv_2y.py   # 2年分OHLCVデータ取得
├── requirements.txt
└── start_trading.bat      # 起動バッチ（タスクスケジューラ登録用）
```

---

## 3. レジーム分類

Pine Script（15M足ベース）が各バーで以下の優先順位でレジームを決定する。
上位のレジームが成立した場合、下位の評価は行わない。

| 優先順位 | レジーム | 判定条件（15M足） | 状態 |
|---|---|---|---|
| 1 | **TREND** | 15M ADX ≥ 25、かつ H1トレンド有効（H1 ADX ≥ 25）、かつ 15M EMA位置がH1方向と一致 | ✅ 稼働中 |
| 2 | **BREAKOUT** | 15M ATR > ATR_MA20 × 1.5倍、かつ ADX平均 < 20、かつ 現在ADX ≥ 20 | ✅ 稼働中 |
| 3 | ~~REVERSAL~~ | ~~ADX低下中（adx_avg5 - adx > 5）~~ | ❌ 廃止（Gate2でreject） |
| 4 | **RANGE** | 上記すべて非該当 | ❌ Gate2でreject |

**設計背景:** REVERSALは逆張りを狙うレジームだが、XAUUSD 5Mでは信頼性が低く、
スコアリング調整で抑制するより廃止して計算コストを削減する判断をした。

### 方向決定ロジック

| レジーム | 方向の決定方法 |
|---|---|
| **TREND** | H1方向が bull かつ 15M close > EMA → buy。H1方向が bear かつ 15M close < EMA → sell |
| **BREAKOUT** | レンジ高値を突破 → buy。レンジ安値を突破 → sell。<br>**v4.3追加:** `bo_direction_locked` 変数でBREAKOUT突入時の方向をロック。<br>リテスト局面で価格がレンジ内に戻っても方向を維持する（旧実装では `direction="none"` になるバグがあった） |
| ~~REVERSAL~~ | ~~H1方向の逆（逆張り）~~ （廃止） |

---

## 4. エントリー条件（Gate1〜3）

### Gate 1（共通フィルター）

| 条件 | 内容 | 設計理由 |
|---|---|---|
| `h1_adx >= 25` | H1足ADXで上位足のトレンド強度を確認 | 弱いマクロ環境でのエントリーを除外。5M足だけ見るとトレンドに見えても、H1でレンジの場合はダマシが多い |

### Gate 2（レジームフィルター）

| 条件 | 内容 |
|---|---|
| `regime ≠ RANGE` | レンジ相場はエントリー不可（即reject） |
| `regime ≠ REVERSAL` | 廃止レジーム（即reject） |
| `direction ≠ none` | 方向未確定は即reject |

### Gate 3（レジーム別SMCフィルター）

#### TREND
以下のSMC条件のいずれか**1つ以上**を満たすこと（OR条件）。
複数条件が揃うほどスコアが高くなりapproveされやすくなる。

| 条件 | シグナル名 |
|---|---|
| CHoCH確認 | `choch_confirmed == true` |
| FVGヒット | `fvg_aligned == true` |
| Zoneヒット | `zone_aligned == true` |
| BOS確認 | `bos_confirmed == true`（v4.3でバグ修正済み） |
| OBヒット | `ob_aligned == true`（v4.3でバグ修正済み） |

**設計背景:** 旧実装ではAND条件で「CHoCH AND FVG/Zone」が必須だったが、
条件が厳しすぎてエントリー機会を逃すケースが多かった。
スコアリングに詳細な評価を委ねることでゲートをOR化した。

#### BREAKOUT

| 条件 |
|---|
| `fvg_aligned == true` または `zone_aligned == true`（リテスト確認必須） |

---

## 5. SMCシグナル検出ロジック（Pine Script）

### CHoCH（Change of Character）

- **定義:** 直近スイングポイントを更新した時点で構造転換と判定
- **上昇CHoCH:** `close > last_swing_high AND close[1] <= last_swing_high`
- **下降CHoCH:** `close < last_swing_low AND close[1] >= last_swing_low`
- チャート上にラベル（CHoCH↑ / CHoCH↓）を表示

### BOS（Break of Structure）

**v4.3バグ修正済み。**

| バージョン | 処理順序 | 問題 |
|---|---|---|
| 修正前 | CHoCH判定 → CHoCHリセット（`last_swing_high := na`） → **BOS評価** | CHoCHとBOSが同一条件（`close > last_swing_high`）で発火するため、CHoCHがlast_swing_highをnaにリセットした後にBOSを評価するとnot na()チェックで常にfalse |
| **修正後** | CHoCH判定 → **BOS評価** → CHoCHリセット | BOS評価をCHoCHリセットより前に実施することで正常発火 |

- **BOS Pending管理:** BOSバーから `bos_timeout_bars`（デフォルト20本=100分）以内にプルバックがなければキャンセル
- `bos_confirmed` = `bos_pending_bull or bos_pending_bear`（Pending中であることを示すフラグ）
- チャート上にラベル（BOS↑ / BOS↓）を表示

### OB（Order Block）

- **Bullish OB:** BOS上昇確認時の1本前の陰線キャンドル本体
- **Bearish OB:** BOS下降確認時の1本前の陽線キャンドル本体
- `ob_aligned`: bos_pending中 かつ 方向一致 かつ closeがOB内にある
- **BOS修正の副次効果:** BOSが発火しなかった旧実装ではOBも0件/日だった。BOS修正と同時にOBも正常発火するようになった

### FVG（Fair Value Gap）

- **Bull FVG:** `high[2] < low[0]` かつサイズ ≥ `ATR × fvg_atr_min_mult`（デフォルト0.3）
- **Bear FVG:** `low[2] > high[0]`
- 50%以上埋まった場合（closeがFVG中値に到達）または `zone_max_age`（200バー）経過で無効化
- **注意:** 現在は5M足ベース（将来的に15M足への変更を検討中）
- ⚠️ **既知の問題:** 同一FVGへの再タッチで再発火する（識別子未実装）

### Zone（需給ゾーン）

- **Demand Zone（買い需要）:** 陰線後に上昇インパルス（`ATR × zone_impulse_mult`以上）が来た場合に確定
- **Supply Zone（売り供給）:** 陽線後に下落インパルスが来た場合に確定
- `zone_impulse_bars`（デフォルト6バー）以内にインパルスが来なければキャンセル
- closeがゾーン下抜け（Demand）または上抜け（Supply）で無効化
- ⚠️ **既知の問題:** FVGと同様に再使用制御なし

### Sweep（流動性スイープ）

- **安値スイープ:** 直近安値をヒゲが貫通し、closeが安値より上に戻る（sell-side liquidity狩り → buy方向シグナル）
- **高値スイープ:** 直近高値をヒゲが貫通し、closeが高値より下に戻る（buy-side liquidity狩り → sell方向シグナル）
- 貫通量 ≥ `ATR × sweep_atr_mult`（デフォルト0.2）を条件とする

---

## 6. スコアリングテーブル

### 判定フロー

```
Gate1（h1_adx確認）
    ↓ 通過
Gate2（レジーム確認）
    ↓ 通過
Gate3（SMC条件確認）
    ↓ 通過
スコア計算（全項目を加算）
    ↓
合計スコア ≥ 0.40 → approve（発注）
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
| FVG単独ペナルティ | `fvg_only_penalty` | **-0.20** | CSV分析: PF=0.55（負け）/ bos・ob・zoneが全て0の場合のみ適用 |
| Zone単独ペナルティ | `zone_only_penalty` | **-0.20** | CSV分析: PF=0.86（負け）/ bos・ob・fvgが全て0の場合のみ適用 |
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

### approve_threshold = 0.40 の設計根拠

閾値を0.40に設定することで、低品質なシグナル（LondonセッションのBOS単独等）を除外できる。

```
bos単独(+0.30) + tokyo(+0.10)        = 0.40 → approve ✅（意図的にギリギリ通過）
bos単独(+0.30) + ny(+0.05) + adx(+0.10) = 0.45 → approve ✅
bos単独(+0.30) + london(-0.15)       = 0.15 → reject ❌（低品質セッションを除外）
fvg+zone(+0.30) + bos(+0.30)         = 0.60 → approve ✅（複合シグナルは通過）
zone単独(-0.20) + bos(+0.30)         = 0.10 → reject ❌（zone単独ペナルティが効く）
```

---

## 7. エグジットロジック

### TRENDレジーム（3ステップ管理）

Optunaで最適化したパラメータ（P2, 2026-03-12）を使用。

| ステップ | トリガー | 内容 |
|---|---|---|
| 1. ブレークイーブン(BE) | 含み益 = ATR×**1.8** | SLをエントリー価格 + ATR×0.15バッファへ移動 |
| 2. 部分決済(50%) | 含み益 = ATR×**3.6** | ポジションの50%を利確 |
| 3. トレーリングストップ | 部分決済後に自動起動 | 高値/安値更新ごとにATR×**1.2**で追随 |
| 最終TP | ATR×**6.0** | 残り50%全決済（実際にはトレーリングSLで先に決済されることが多い） |

### BREAKOUT / REVERSALレジーム

BEと部分決済・トレーリングは行わない。固定SL/TPのみ。

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

## 8. リスク管理

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

```python
# config.py の SYSTEM_CONFIG 内
"demo_mode": True,   # ← False に戻すだけでリアル口座モードに切り替わる
```

| `demo_mode` | 日次損失制限 | 連続損失制限 | ログ出力 |
|---|---|---|---|
| `True`（現在） | **無効**（スキップ） | **無効**（スキップ） | `demo_mode: 日次損失制限スキップ` / `demo_mode: 連続損失制限スキップ` |
| `False` | 有効（-5%で停止） | 有効（3連敗で停止） | なし |

> ⚠️ **リアル口座移行前に必ず `demo_mode: False` に変更すること。**

### ニュースブラックアウト（固定スケジュール）

`HIGH_IMPACT_UTC_TIMES`（config.py）で設定された固定時間帯は発注をブロック。
動的なカレンダーAPI連携は未実装（Phase 1-2で予定）。

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

## 9. Discord通知

`.env` に `DISCORD_WEBHOOK_URL` を設定すると以下の7イベントで通知が送信される。
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

---

## 10. CSV分析基盤（2026-03-21 新規追加）

### 背景

スコアリングテーブルの値は設計者の経験則で決めていたが、実際のシグナルデータで
検証したところ**セッション評価が現実と真逆**であることが判明した。
例: London/NYを最高評価（+0.10）にしていたが実績PF=0.89（負け）、
Tokyoを最低評価（-0.10）にしていたが実績PF=1.14（勝ち）。
この問題を解決するためCSV分析基盤を構築した。

### csv_exporter.pine

TradingView Proの「チャートデータをエクスポート」機能用に作成した独立ペインの
インジケーター。`plot()` で全条件を数値エンコードして出力し、
過去データを一括CSVとして取得できる（`alert()` = リアルタイムのみ、の制限を回避）。

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
# 使用方法
py python/csv_analyzer.py data/export.csv
```

**分析レポート項目:**
- 全体サマリー（勝率 / PF / 期待値 / 総件数）
- レジーム別成績
- セッション別成績
- SMC条件の組み合わせ別成績（上位15位）
- H1方向一致/不一致の比較
- スコア閾値シミュレーション（件数/日 × PF のトレードオフ）
- 日別エントリー数分布

### 分析実績（2025-12-07〜2026-03-20、59日間）

2年分のXAUUSD 5Mデータに対して `csv_exporter.pine` で生成したCSVを分析した結果。
この発見に基づいてスコアリングを全面再設計（Phase 2完了）。

**SMC条件組み合わせ別 実績:**

| 条件組み合わせ | PF | 期待値 | 件数 |
|---|---|---|---|
| **fvg+zone同時** | **4.81** | **+2.42R** | 17件 |
| bos+sweep | 1.48 | +0.65R | 76件 |
| bos単独 | 1.22 | +0.32R | 687件 |
| choch+bos | 1.13 | +0.20R | 111件 |
| zone単独 | 0.86 | -0.25R | 183件 |
| zone+bos | 0.60 | -0.76R | 166件 |
| fvg+bos | 0.84 | -0.27R | 69件 |
| fvg単独 | 0.55 | -0.88R | 118件 |

**セッション別 実績（旧評価との比較）:**

| セッション | 実績PF | 旧スコア | 新スコア | 変更理由 |
|---|---|---|---|---|
| tokyo | 1.14 | -0.10 | **+0.10** | 実績優良→逆転 |
| ny | 1.07 | ±0.00 | **+0.05** | 実績良好→加点 |
| off | 0.93 | -0.20 | **-0.05** | 実績は軽微な負け→緩和 |
| london_ny | 0.89 | +0.10 | **-0.10** | 実績不振→逆転 |
| london | 0.83 | +0.05 | **-0.15** | 実績最悪→逆転 |

---

## 11. 開発ロードマップ

| フェーズ | 内容 | 状態 |
|---|---|---|
| Phase 0-1 | `csv_exporter.pine` 作成（全バーCSV出力用インジ） | ✅ 完了 |
| Phase 0-2 | `csv_analyzer.py` 作成・59日分データ分析実行 | ✅ 完了 |
| Phase 1-1 | BOS/OBバグ修正（CHoCHリセット前にBOS評価する処理順序修正） | ✅ 完了 |
| Phase 2   | スコアリング全面再設計（CSVデータ根拠）・閾値0.40に変更 | ✅ 完了 |
| Phase 1-2 | ニュースフィルター動的実装（経済指標カレンダーAPI連携） | 🔲 未着手 |
| Phase 1-3 | FVG/Zone再使用制御＋重複エントリーブロック実装 | 🔲 未着手 |
| Phase 3   | BREAKOUTエグジット再設計（TP縮小＋BE追加） | 🔲 未着手 |
| Phase 4-1 | FVG/Zoneを15M足ベースに変更（5M足のノイズ対策） | 🔲 検討中 |
| Phase 4-2 | ピラミッディング（FVG識別子実装後に実施） | 🔲 未着手 |
| Phase 5   | RSIダイバージェンス実装（スコア0.00の項目を有効化） | 🔲 未着手 |

---

## 12. 既知の問題・TODO

優先度順に記載する。

| 優先度 | 項目 | 詳細 |
|---|---|---|
| 🔴 高 | FVG/Zone再使用制御なし | 同一FVG/Zoneへの再タッチのたびに再発火する。識別子（bar_index等）で1回のみ発火に変更が必要 |
| 🔴 高 | 重複エントリーブロック未実装 | 同方向ポジションが既存の場合でも新規エントリーする。max_positionsでの上限管理のみ |
| 🟡 中 | `news_nearby` 動的実装なし | NFP/FOCMの固定ブラックアウトのみ。他の重要経済指標・BOJ・ECBは非対応 |
| 🟡 中 | `rsi_divergence` 未実装 | Pine Scriptで常にfalse固定。スコアテーブルに0.00で登録済みだが機能していない |
| 🟡 中 | BREAKOUTのTP×6.0 | 5M足では非現実的に長いTP設定。Phase 3で縮小＋BE追加を予定 |
| 🟢 低 | 5M足FVG/Zoneのノイズ問題 | 5M足は細かすぎてノイズが多い。15M足への変更を検討中だが後方互換性の問題がある |
| 🟢 低 | タスクスケジューラ自動起動 | VPS再起動後の自動起動。`start_trading.bat` は作成済みだが登録確認が必要 |

---

## 13. セットアップ手順

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

```bash
# .env.example をコピーして設定
copy .env.example .env
```

```env
# .env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...  # オプション
```

### 起動方法

```bat
rem 環境変数設定してサーバー起動
set PYTHONPATH=C:\Users\Administrator\trading_system
cd C:\Users\Administrator\trading_system
py python/app.py > flask.log 2>&1
```

```bash
# 起動確認
curl http://localhost:80/health
```

### TradingViewアラート設定

1. GOLD# 5Mチャートに `pine/alert_sender_5m.pine` を追加
2. `pine/regime_detector.pine` を追加（サブインジ）
3. アラートを作成: 条件 = "AI Alert Sender [XAUUSD/5M]" → alert()関数の呼び出し
4. Webhook URL = `http://150.66.32.144/webhook`
5. メッセージ欄は空白（Pine Script内で `alert(json)` を直接送信するため不要）

### CSV分析（オフライン分析用）

1. TradingView Proで `pine/csv_exporter.pine` を追加
2. 5Mチャートで「チャートデータをエクスポート」→ CSVを `data/` に保存
3. 分析実行:

```bash
py python/csv_analyzer.py data/export.csv
```

---

## 14. テスト実行

```bash
cd trading_system
py -m pytest tests/ -v
```

### テストファイル一覧

| ファイル | テスト数 | 対象 |
|---|---|---|
| `test_scoring_engine.py` | **29件** | Gate1〜3判定・スコア計算・閾値判定・新ペナルティ関数 |
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

**現在の状態:** `test_scoring_engine.py` 全29件 PASSED（2026-03-21確認済み）

---

## 15. バージョン履歴

| バージョン | 日付 | 主な変更点 |
|---|---|---|
| **v4.3** | 2026-03 | BOS/OBバグ修正（CHoCHリセット前にBOS評価）・BREAKOUTdirectionバグ修正（bo_direction_locked導入）・scoring_history全件記録・BOJ固定ブラックアウト削除・FOMC時間修正（21-24→18-20）・損失アラート通知削除 |
| **v4.2** | 2026-02 | REVERSAL廃止（Gate2でreject）・Pine Script発火条件OR化・approve閾値0.15に引き下げ・描画統合（FVG/Zone/OB/BOS/CHoCHラベル）・シンボル変換（XAUUSD→GOLD#）・JPY口座USD換算バグ修正 |
| **v4.1** | 2026-01 | should_alert条件をレジーム+direction確定のみに緩和（Gate3重複解消）・Discord通知実装（7イベント）・Windows VPS（ABLENET）移行完了 |
| **v4.0** | 2025-12 | Pine Script→Flask直接送信アーキテクチャに刷新・LLM廃止・マルチレジーム対応・BOS/OB実装・Optuna最適化 |
| v3.5 | 2025-11 | `structurize()` をルールベースデフォルト化・MetaOptimizer追加 |
| v3.0 | 2025-10 | LLMを構造化専任に変更・数値ルールベーススコアリング導入・ライブ型バックテスター追加 |
| v2.2 | 2025-09 | SQLite接続プール化・ニュースフィルターフェイルセーフ・日次損失上限-5% |
| v2.1 | 2025-08 | ユニットテスト追加・backtester.py・param_optimizer.py |
| v2.0 | 2025-07 | LLM判定廃止→数値ルール化 |
| v1.x | 2025-06 | 初期実装（LLMベース判定・Forex対応） |

---

## システム概要

Pine Script（5M足）が生成したアラートJSONをWindows VPS上のFlask Webhookで受信し、
数値ルールベースのスコアリングエンジン（LLM不使用）でエントリー可否を判定。
承認されたシグナルを同一マシン上のMT5に発注する。

```
TradingView (Pine Script 5M / v4.3)
        │  Webhook (HTTP port 80)
        ▼
Windows VPS / ABLENET (150.66.32.144)
├── python/app.py（Flask）
├── scoring_engine.py（Gate1〜3 + スコアリング）
├── executor.py（MT5発注 + Discord通知）
├── position_manager.py（ポジション管理 + Discord通知）
├── health_monitor.py（MT5死活監視 + Discord通知）
└── MT5（XMTrading デモ口座 GOLD#）
```

---

## レジーム分類

Pine Script が各バーで以下の優先順位でレジームを決定する。

> **v4.3修正**: BREAKOUTレジームでリテスト局面に価格がレンジ内へ戻った際に `direction="none"` となるバグを修正。
> `bo_direction_locked` 変数でBREAKOUT突入時の方向をロックし、レジームが変わるまで維持する実装に変更。

| 優先順位 | レジーム | 条件 | 状態 |
|---|---|---|---|
| 1 | **TREND** | H1 ADX ≥ 25 かつ明確なトレンド構造 | 稼働中 |
| 2 | **BREAKOUT** | ローカル高値/安値ブレイク後のリテスト | 稼働中 |
| 3 | ~~REVERSAL~~ | ~~RSIダイバージェンス or 流動性スイープ検出~~ | **廃止（Gate2でreject）** |
| 4 | **RANGE** | 上記いずれにも該当しない | Gate2でreject |

---

## エントリー条件

### Gate 1（共通）

| 条件 | 内容 |
|---|---|
| `h1_adx >= 25` | H1 ADX が 25 以上（トレンド強度確認） |

### Gate 2（共通）

| 条件 | 内容 |
|---|---|
| `regime ≠ RANGE` | RANGE レジームは即 reject |
| `regime ≠ REVERSAL` | REVERSAL レジームは即 reject（廃止） |
| `direction ≠ none` | 方向未確定は即 reject |

### Gate 3（レジーム別）

#### TREND
SMC条件のいずれか1つを充足すること（OR条件）。

| 条件 |
|---|
| `choch_confirmed == true` |
| `fvg_aligned == true` |
| `zone_aligned == true` |
| `bos_confirmed == true` |
| `ob_aligned == true` |

※ 複数条件が揃うほどスコアが高くなり、approveされやすくなる。

#### BREAKOUT
| 条件 |
|---|
| `fvg_aligned == true` または `zone_aligned == true` |

---

## スコアリングテーブル

ゲートを通過したアラートに対して以下のルールでスコアを加算し、
合計スコアで最終判定を行う。

| 条件 | キー | 加減点 |
|---|---|---|
| CHoCH確認済み | `choch_strong` | +0.20 |
| RSIダイバージェンス | `rsi_divergence` | +0.15 |
| FVG × Zone 重複ヒット | `fvg_and_zone_overlap` | +0.15 |
| 15M ADX 25〜35 | `adx_normal` | +0.10 |
| REVERSALかつADX>35 | `adx_reversal_penalty` | −0.10 |
| H1方向とエントリー方向一致 | `h1_direction_aligned` | +0.10 |
| London/NYセッション | `session_london_ny` | +0.10 |
| Londonセッション | `session_london` | +0.05 |
| NYセッション | `session_ny` | ±0.00 |
| 東京セッション | `session_tokyo` | −0.10 |
| オフアワーズ | `session_off` | −0.20 |
| ATR ratio 0.8〜1.5 | `atr_ratio_normal` | +0.05 |
| ATR ratio > 1.5 | `atr_ratio_high` | −0.05 |
| 高インパクトニュース前後30分 | `news_nearby` | −0.30 |
| BOS確認済み | `bos_confirmed` | +0.20 |
| OBヒット | `ob_aligned` | +0.20 |
| OB + FVG 重複ボーナス | `ob_and_fvg` | +0.10 |

### 判定閾値

| 判定 | スコア |
|---|---|
| **approve** | ≥ 0.15 |
| **wait** | ≥ 0.00 |
| **reject** | < 0.00 |

---

## エグジットロジック

エグジットパラメータは Optuna（n_trials=300, objective: PF×log(trades)）で最適化済み（P2, 2026-03-12）。

### TREND（部分決済＋トレーリング）

| ステップ | タイミング | 内容 |
|---|---|---|
| ブレークイーブン (BE) | 含み益 = ATR × **1.8** | SLをエントリー価格 + ATR×0.15バッファへ移動 |
| 部分決済 (50%) | 含み益 = ATR × **3.6** | ポジションの50%を利確 |
| トレーリングストップ | 部分決済後 | 高値更新ごとにSLをATR × **1.2**で追随 |
| 最終TP | ATR × **6.0** | 残り50%を全決済（実質トレーリングで決済） |

### REVERSAL / BREAKOUT（固定SL/TPのみ）

BE・部分決済・トレーリングストップは行わない。

| パラメータ | 値 |
|---|---|
| SL | ATR × 2.7 |
| TP | ATR × 6.0 |

※ REVERSAL/BREAKOUTはエントリー時点で固定SL・固定TPをセットして放置する設計。

### SL（共通）

```
SL距離 = ATR × 2.7
制約: min 8ドル / max 80ドル
```

### セッション別 SL/TP 乗数補正

| セッション | SL乗数 | TP乗数 |
|---|---|---|
| Asia | ×0.75 | ×0.75 |
| London | ×1.00 | ×1.00 |
| London_NY | ×1.30 | ×1.30 |
| NY | ×1.00 | ×1.00 |
| Off_hours | ×0.75 | ×0.75 |

---

## バックテスト結果（P2 最適化後）

> ⚠️ テストデータ（`alerts_test.csv`）は約50取引日分の限定的なサンプルです。
> 本番稼働前に6ヶ月以上のライブデータで再検証してください。

| 指標 | 値 |
|---|---|
| 総トレード数 | 94件 |
| 勝率 | 63.8% |
| プロフィットファクター (PF) | **1.435** |
| 最大ドローダウン (MDD) | 17.55% |
| 1日平均トレード数 | 1.88件 |
| TP到達率 | — |

### ゲート通過率（テストデータ）

| ゲート | 件数 |
|---|---|
| 入力アラート総数 | 108件 |
| Gate1 不通過 (h1_adx<25) | 0件 |
| Gate2 不通過 (RANGE) | 1件 |
| Gate3 不通過 | 0件 |
| スコア不足 (reject) | 0件 |
| wait | 12件 |
| **approve** | **95件** |

### 最適化パラメータ（Optuna #1）

```
sl_mult=2.7  tp_mult=6.0  be_trigger=1.8
partial_tp=3.6  trailing=1.2
```

---

## Pine Script アラートJSON フィールド一覧

Pine Script (`pine/alert_sender_5m.pine`) が Webhook に送信するフラットJSON。

Webhook受信時、スコアリング結果は approve / reject / wait 全ケースで `scoring_history` テーブルに記録される。

| カラム | 内容 |
|---|---|
| `created_at` | 記録日時（UTC ISO8601） |
| `signal_direction` | "buy" / "sell" |
| `regime` | "TREND" / "BREAKOUT" / "REVERSAL" / "RANGE" |
| `total_score` | スコアリング合計点 |
| `decision` | "approve" / "wait" / "reject" |
| `breakdown_json` | 各採点項目の内訳（JSON） |

| フィールド | 型 | 説明 |
|---|---|---|
| `symbol` | string | シンボル名（例: "GOLD#"）（XMTrading仕様） |
| `direction` | string | "buy" / "sell" |
| `regime` | string | "TREND" / "BREAKOUT" / "REVERSAL" / "RANGE" |
| `h1_adx` | float | H1 ADX値 |
| `m15_adx` | float | 15M ADX値 |
| `h1_direction` | string | "bull" / "bear" |
| `choch_confirmed` | bool | CHoCH（構造転換）確認 |
| `bos_confirmed` | bool | BOS（構造ブレイク）確認 |
| `ob_aligned` | bool | オーダーブロックヒット確認 |
| `fvg_aligned` | bool | FVG（価格ギャップ）アライン |
| `zone_aligned` | bool | サポート/レジスタンスゾーンアライン |
| `sweep_detected` | bool | 流動性スイープ検出 |
| `rsi_divergence` | bool | RSIダイバージェンス（※現在 false 固定） |
| `news_nearby` | bool | 高インパクトニュース前後30分（※現在 false 固定） |
| `session` | string | "london_ny" / "london" / "ny" / "tokyo" / "off" |
| `atr_ratio` | float | 15M ATR / ATR MA20 |
| `atr5` | float | 5M ATR値（ドル） |
| `price` | float | 現在価格 |
| `time` | string | アラート時刻（ISO8601） |

---

## セットアップ手順

### 1. Windows VPS側（ABLENET Win1 / 150.66.32.144）

| 項目 | 内容 |
|---|---|
| OS | Windows Server 2022 |
| Python | 3.14 |
| MT5 | XMTrading インストール済み（デモ口座 75503697 ログイン済み） |
| Webhook ポート | 80 |
| 自動起動 | タスクスケジューラ「TradingSystem」登録済み |
| バッチファイル | `C:\Users\Administrator\start_trading.bat` |

```bat
rem start_trading.bat（タスクスケジューラ登録済み / VPS再起動時に自動実行）
@echo off
set PYTHONPATH=C:\Users\Administrator\trading_system
cd C:\Users\Administrator\trading_system
git pull
py python/app.py
```

```bat
rem 起動確認
curl -s http://localhost:80/health
```

※ メインのFlaskエントリーポイントは `python/app.py`。
※ MT5はWindows VPS上で起動済み（同一マシン）。

### 2. TradingView 設定

- Pine Script `pine/alert_sender_5m.pine` をチャートに追加
- アラート設定 → Webhook URL: `http://150.66.32.144/webhook`
- メッセージ: `{{strategy.order.alert_message}}`

### 3. 動作確認

```bash
# サーバー死活確認
curl http://150.66.32.144/health

# テスト Webhook 送信
curl -X POST http://150.66.32.144/webhook \
  -H "Content-Type: application/json" \
  -d '{"symbol":"GOLD#","direction":"buy","regime":"TREND","h1_adx":30}'
```

### 4. バックテスト実行

```bash
# 標準実行（alerts_test.csv + ohlcv_GOLD_5m.csv）
python backtester_live.py

# Optuna最適化（n_trials=300, 所要時間: 約10分）
python optimize_exit_params.py
```

---

## ファイル構成

```
trading_system/
├── python/
│   └── app.py                # Flask メインエントリーポイント（python/app.py）
├── config.py                 # 全設定の一元管理（SCORING_CONFIG 含む）
├── scoring_engine.py         # ゲートチェック + スコアリングエンジン（v4.2）
├── executor.py               # MT5 注文執行 + Discord通知
├── position_manager.py       # BE移動・部分決済・トレーリング + Discord通知
├── discord_notifier.py       # Discord Webhook通知（DISCORD_WEBHOOK_URL設定時のみ有効）
├── risk_manager.py           # 日次損失上限・連続負け・ギャップリスク
├── news_filter.py            # 高インパクトニュースフィルター
├── signal_collector.py       # Webhook 受信バッファ（500ms窓）
├── batch_processor.py        # バッチ処理パイプライン
├── backtester_live.py        # ライブ型バックテスター（scoring_engine と同一コード）
├── optimize_exit_params.py   # Optuna エグジットパラメータ最適化
├── context_builder.py        # MT5 市場データ収集
├── ai_judge.py               # パイプライン窓口（後方互換）
├── revaluator.py             # wait シグナル再評価
├── health_monitor.py         # MT5 接続監視・自動再接続
├── market_hours.py           # 市場クローズ判定
├── notifier.py               # 通知（Discord / LINE）
├── dashboard.py              # ブラウザダッシュボード
├── database.py               # SQLite 接続管理
├── logger_module.py          # ログ記録（SQLite）
├── download_ohlcv.py         # yfinance OHLCV データ取得
├── pine/
│   └── alert_sender_5m.pine  # Pine Script v6（5M足アラート送信 + 描画統合）
├── tests/
│   ├── test_scoring_engine.py    # scoring_engine ユニットテスト（17件）
│   ├── test_risk_manager.py      # risk_manager ユニットテスト
│   ├── test_executor.py          # executor ユニットテスト
│   └── ...
├── alerts_test.csv           # テスト用アラート履歴（約50取引日分）
├── ohlcv_GOLD_5m.csv         # バックテスト用 OHLCV データ
├── result_p2.csv             # Optuna P2 最適化バックテスト結果
└── requirements.txt
```

---

## エンドポイント

| URL | メソッド | 説明 |
|---|---|---|
| `/webhook` | POST | Pine Script アラート受信 |
| `/health` | GET | サーバー死活確認（200=OK） |
| `/dashboard` | GET | ブラウザダッシュボード |

---

## Discord通知

`.env` に `DISCORD_WEBHOOK_URL` を設定すると主要イベントを Discord に通知する（未設定時はスキップ）。

```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

| タイミング | タイトル | 色 |
|---|---|---|
| Webhook受信（スコア前） | 📡 シグナル受信 | グレー |
| スコアリング承認 | ✅ エントリー承認 | 緑 |
| スコアリング否決 | ❌ エントリー否決 | 赤 |
| 発注成功 | 🚀 発注成功 | 緑 |
| 発注失敗 | ⚠️ 発注失敗 | 黄 |
| ポジション決済（利確） | 💰 決済（利確） | 緑 |
| ポジション決済（損切） | 🔴 決済（損切） | 赤 |
| MT5接続断 | 🆘 システムアラート | 赤 |

> **v4.3変更**: ポジション含み損アラート（`notify_loss_alert`）を削除。10秒ポーリングのたびに含み損ポジションで連射されDiscordが大量通知で埋まるため。

---

## 多重安全装置

エントリー前に以下のガードが適用される。

1. **ゲートチェック** — Gate1/2/3 の全条件をクリアしないと reject
2. **ニュースフィルター** — 高インパクト指標の前後30分はブロック。固定ブラックアウト時間帯は以下のとおり（`HIGH_IMPACT_UTC_TIMES` で設定）：

   | 時間帯（UTC） | イベント |
   |---|---|
   | 水曜 18:00〜20:00 | FOMC（旧: 21〜24 → v4.3で修正） |

   ※ BOJ固定ブラックアウト（毎週木曜3〜6時UTC）は v4.3 で削除。BOJは毎週開催ではないため固定ブロックは不適切。
3. **市場時間チェック** — デイリーブレイク（23:45〜1:00 UTC）はスキップ
4. **当日損失制限** — 残高の −5% 超過で自動停止
5. **連続損失制限** — 3連敗で自動停止
6. **週明けギャップ** — 月曜早朝のギャップが $15 超でブロック
7. **複数ポジション管理** — 最大5ポジション・合計リスク上限10%

---

## 開発ロードマップ

| フェーズ | 内容 | 状態 |
|---|---|---|
| P1 | Optuna 探索範囲設計 | ✅ 完了 |
| P2 | Optuna 最適化 (n_trials=300) → config 反映 | ✅ 完了（PF1.435） |
| P2.5 | ゲート通過率計測（backtester_live.py 拡張） | ✅ 完了 |
| P3 | BOS / Order Block 実装（Pine + scoring） | ✅ 完了 |
| P3.5 | Windows VPS（ABLENET）構築・MT5デモ口座接続・自動売買待機 | ✅ 完了 |
| P3.6 | should_alert条件緩和・Discord通知実装 | ✅ 完了 |
| P3.7 | REVERSAL廃止・BUGfixシリーズ（JPY換算・シンボル変換・Gate3緩和） | ✅ 完了 |
| P3.8 | Pine Script描画統合・Discord通知改善 | ✅ 完了 |
| P4 | FVG/Zone生涯1回発火・重複エントリーブロック | 未着手 |
| P5 | RSIダイバージェンス実装 | 未着手 |
| P6 | ニュースフィルター動的実装 | 未着手 |
| P7 | 本番6ヶ月データでの再バックテスト・検証 | 未着手 |

---

## 既知の問題・TODO

優先度順。

| 優先度 | 項目 | 状況 |
|---|---|---|
| 1 | `rsi_divergence` | Pine Script で常に `false` 固定。RSIダイバージェンス検出ロジック未実装 |
| 2 | `news_nearby` | Python側で木曜12:00〜13:00 UTCの固定ブラックアウトのみ実装。動的ニュースフィルター未実装 |
| 3 | FVG/Zone生涯1回発火 | 同一FVG/Zoneへの再タッチで再発火する（実装予定） |
| 4 | 重複エントリーブロック | 同一方向のポジションが既にある場合のブロック未実装 |
| 5 | スコアリングテーブル | 全レジーム共通採点。レジーム別の加減点設計が未実装 |

---

## バージョン履歴

| バージョン | 主な変更点 |
|---|---|
| **v4.3** | BOJ固定ブラックアウト削除・FOMC時間修正（21-24→18-20）・scoring_history記録追加（approve/reject/wait全ケース）・BREAKOUTdirectionバグ修正（bo_direction_locked導入）・損失アラート通知削除・起動bat git pull追加 |
| **v4.2** | REVERSAL廃止（Gate2でreject）。Pine Script発火条件をSMC条件OR化。approve閾値0.15に引き下げ。描画統合（FVG/Zone/OB/BOS/CHoCHラベル）。シンボル変換（XAUUSD→GOLD#）。Discord通知改善（発注結果詳細化）。JPY口座USD換算バグ修正 |
| **v4.1** | should_alert条件をレジーム+direction確定のみに緩和（Gate3重複解消）。Discord通知実装（7イベント）。Windows VPS（ABLENET）移行完了 |
| **v4.0** | Pine Script → Flask 直接送信アーキテクチャに刷新。LLM廃止。マルチレジーム対応スコアリングエンジン。BOS/OB実装。Optuna最適化 PF1.435 |
| v3.5 | `structurize()` をルールベースデフォルト化。MetaOptimizer 追加 |
| v3.0 | LLMを構造化専任に変更。数値ルールベーススコアリング導入。ライブ型バックテスター追加 |
| v2.2 | SQLite接続プール化。ニュースフィルターフェイルセーフ。日次損失上限 -5% |
| v2.1 | ユニットテスト追加（49件）。backtester.py。param_optimizer.py |
| v2.0 | LLM判定廃止→数値ルール化 |
