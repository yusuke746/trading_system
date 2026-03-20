# AI Trading System v4.3

TradingView Pine Script × ルールベーススコアリング × MT5
XMTrading / GOLD（XAUUSD）専用 完全自動取引システム

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
