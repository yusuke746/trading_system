# AI Trading System v2.0

TradingViewアラート × GPT-4o-mini × MT5  
XMTrading / XAUUSD 専用 完全自動取引システム

---

## 🔰 このプログラムを一言で言うと

> **TradingViewが「チャンスかも」と叫んだら、AIが「本当に勝てるか」を判断し、勝てると判断したら自動でゴールド（金）を売買するシステムです。**

---

## 📖 わかりやすい説明

### 全体の流れ（5ステップ）

```
① TradingViewアラート受信
        ↓
② 500msバッファに溜める（まとめて処理）
        ↓
③ GPT-4o-miniに「エントリーすべき？」と聞く
        ↓
④ AIが承認 → MT5でゴールドを売買
        ↓
⑤ ポジション管理（利確・損切り・トレーリング）
```

### 各パーツのやさしい説明

| ファイル | 何をするところ？ |
|---|---|
| `app.py` | **司令塔**。全体を起動・管理する |
| `signal_collector.py` | **受付係**。TradingViewの合図を500ms待って1つにまとめる |
| `batch_processor.py` | **コーディネーター**。まとめた合図をAIに渡す段取りをする |
| `context_builder.py` | **情報収集係**。MT5から相場の状況データを集めてAIに渡す |
| `prompt_builder.py` | **翻訳係**。相場データをAIが読めるプロンプトに変換する |
| `ai_judge.py` | **AIの窓口**。GPT-4o-miniに「売り？買い？待つ？」を聞く |
| `executor.py` | **注文係**。AIがOKを出したら実際にMT5で注文を出す |
| `position_manager.py` | **管理係**。ポジションをずっと監視して利確・損切りを自動管理する |
| `risk_manager.py` | **リスク番人**。1日の損失上限・連続負け・週明けギャップを監視する |
| `news_filter.py` | **ニュース番人**。重要指標発表の前後30分はエントリーをブロックする |
| `revaluator.py` | **再審査係**。AIが「もう少し待って」と言ったシグナルを定期的に再評価する |
| `loss_analyzer.py` | **振り返り係**。損切りが出るたびにAIに「なぜ負けたか」を分析させる |
| `health_monitor.py` | **見張り役**。MT5接続が切れたら自動で再接続してLINEに通知する |
| `dashboard.py` | **ダッシュボード**。ブラウザで状態・損益・分析をリアルタイム確認できる |

### ポジション管理の3ステップ（わかりやすく）

```
エントリー後
  ┌──────────────────────────────────────────────────┐
  │ STEP1: 含み益がATR×1.0になったら                  │
  │        → ストップロスをエントリー価格付近に移動   │
  │          （もう絶対に損しない状態＝ブレークイーブン）│
  ├──────────────────────────────────────────────────┤
  │ STEP2: 含み益がATR×2.0になったら                  │
  │        → ポジションの50%を利益確定                │
  │          （確実に利益を取る）                      │
  ├──────────────────────────────────────────────────┤
  │ STEP3: 残り50%はトレーリングストップで追いかける   │
  │        → 高値更新のたびにSLを引き上げる            │
  │          （利益を最大限に伸ばす）                  │
  └──────────────────────────────────────────────────┘
```

### 多重安全装置

このシステムには注文を出す前に **5つのガード** があります：

1. 🗞 **ニュースフィルター** — 重要経済指標の前後30分は一切エントリーしない
2. 🕐 **市場時間チェック** — デイリーブレイク（23:45〜1:00 UTC）はエントリーしない
3. 📉 **当日損失制限** — 1日の損失が $200 を超えたら自動停止
4. 🔴 **連続損失制限** — 3連敗したら自動停止
5. 📊 **ギャップリスク** — 月曜早朝の週明けギャップが $15 以上なら停止

---

## 📊 システム評価

### 総合評価: ★★★★☆ (4.0 / 5.0)

---

### ✅ 強み

| 項目 | 評価 | 詳細 |
|---|---|---|
| アーキテクチャ設計 | ★★★★★ | 責務が明確に分離されており、各モジュールが独立して動作する。テストや改修がしやすい。 |
| リスク管理の多層性 | ★★★★★ | ニュースフィルター・当日損失上限・連続損失・ギャップリスクと4層の安全装置を持つ。 |
| AI活用の設計 | ★★★★☆ | 単なる「エントリー判断」だけでなく、waitの再評価や負けトレードの振り返り分析にも活用している点が優れている。 |
| ポジション管理 | ★★★★☆ | BE移動→部分決済→トレーリングの3ステップ管理は実践的で損益改善に直結する。 |
| エラー耐性 | ★★★★☆ | MT5未接続時のテストモード、APIエラー時のフォールバック、バッファの差し戻しなど、障害時の継続性が考慮されている。 |
| 観測性（ダッシュボード・ログ） | ★★★★☆ | SQLiteへの全履歴記録 + ブラウザダッシュボードにより、稼働状況をリアルタイムで把握できる。 |

---

### ⚠️ 改善余地

| 項目 | 評価 | 詳細 |
|---|---|---|
| XAUUSD専用設計 | ★★★☆☆ | シンボルや計算式がゴールド専用にハードコードされており、他の通貨ペアへの転用が難しい。 |
| テストコードの不足 | ★★☆☆☆ | 自動テストが存在しない。リスク管理やポジション計算など、バグが致命的なロジックにユニットテストが欲しい。 |
| AIプロンプトの固定化 | ★★★☆☆ | `prompt_builder.py` のプロンプトが固定文字列のため、市場環境の変化に対して手動更新が必要。 |
| バックテスト機能なし | ★★★☆☆ | 過去データでの戦略検証機能がなく、パラメータ調整が実運用ベースになっている。 |
| 1ポジション制限 | ★★★☆☆ | `max_positions=1` の固定設定。複数戦略の同時運用や分散エントリーには対応していない。 |

---

### 📈 各領域スコア

```
設計・可読性    ████████████████████  90点
リスク管理      ████████████████████  85点
AI活用度        ████████████████      80点
ポジション管理  ████████████████      80点
テスト・品質    ████████              40点
汎用性          ████████████          60点
─────────────────────────────────────
平均            ████████████████      73点 / 100点
実用的総合評価  ████████████████████  80点 / 100点（★★★★☆）
```

> **注記**: 実用的総合評価は技術スコア平均（73点）に加え、「動く・止まらない・安全に負ける」という実運用上の信頼性と多層リスク設計の高さを加味して算出しています。

---

### 🎯 まとめ

このシステムは **「まず動かして実績を積む」フェーズとして非常に完成度が高い** です。  
特にリスク管理の多層設計と、AIを使った振り返り分析の仕組みは、手動トレードでは実現しにくい優位性です。

以下の3機能が v2.1 で追加されました：
1. ✅ **ユニットテストの追加**（`risk_manager.py`・`executor.py` で49テスト）
2. ✅ **バックテスト機能の実装**（`backtester.py`）
3. ✅ **パラメータの動的最適化**（`param_optimizer.py`）

---

## 🧪 ユニットテスト

### 実行方法

```bash
cd trading_system
python -m pytest tests/ -v
```

### テスト一覧（49件）

| ファイル | テストクラス | 件数 | 内容 |
|---|---|---|---|
| `tests/test_risk_manager.py` | TestCheckDailyLossLimit | 7件 | 当日損失上限チェック（正常・超過・DB誤り） |
| `tests/test_risk_manager.py` | TestCheckConsecutiveLosses | 7件 | 連続損失チェック（各パターン） |
| `tests/test_risk_manager.py` | TestCheckGapRisk | 5件 | 週明けギャップチェック（月曜・他曜日・MT5なし） |
| `tests/test_risk_manager.py` | TestRunAllRiskChecks | 5件 | 統合チェック（各ブロック条件・優先順位） |
| `tests/test_executor.py` | TestBuildOrderParams | 18件 | SL/TP計算・ロット計算・ATRフィルター・指値注文 |
| `tests/test_executor.py` | TestPreExecutionCheck | 6件 | 執行前チェック順序・各ブロック条件 |

---

## 📈 バックテスト（backtester.py）

過去の OHLCV データに対して ATR ベースの戦略をシミュレートし、パラメータセットの損益・勝率・最大ドローダウンなどを計算します。

### CLIの使い方

```bash
# CSVファイルを使ったバックテスト（列: time,open,high,low,close,volume）
python backtester.py --csv data.csv

# ATR SL/TP 乗数を指定
python backtester.py --csv data.csv --sl-mult 2.0 --tp-mult 3.0

# RSI 逆張り戦略でテスト
python backtester.py --csv data.csv --strategy rsi

# グリッドサーチ（SL/TP乗数の全組み合わせを比較）
python backtester.py --csv data.csv --grid

# MT5から直接データを取得（要MT5接続）
python backtester.py --mt5 --bars 2000
```

### 出力例

```
==================================================
  バックテスト結果
==================================================
  取引数         : 42
  勝率           : 61.9%
  総損益         : $+1,234.56
  プロフィットF  : 1.843
  最大ドローダウン: $380.00 (3.8%)
  シャープレシオ : 0.421
--------------------------------------------------
  SL乗数         : 2.0
  TP乗数         : 3.0
==================================================
```

### ライブラリとしての使い方

```python
from backtester import BacktestEngine, load_csv, grid_search

df     = load_csv("data.csv")
engine = BacktestEngine(df, {"atr_sl_multiplier": 2.0, "atr_tp_multiplier": 3.0})
result = engine.run()
print(result.summary())

# グリッドサーチ
results = grid_search(df)
print(f"最良パラメータ: SL×{results[0]['sl_mult']}  TP×{results[0]['tp_mult']}")
```

### ダッシュボード API

```
GET /dashboard/api/backtest?bars=1000&sl_mult=2.0&tp_mult=3.0&strategy=breakout
GET /dashboard/api/backtest?grid=1
```

---

## ⚙️ パラメータ動的最適化（param_optimizer.py）

市場環境（ATR パーセンタイル・トレンド強度）と最近のトレード成績に基づいて、ATR 乗数を自動調整します。  
`executor.py` の注文パラメータ計算に自動的に統合されており、5分キャッシュで効率よく動作します。

### 調整ルール

| 条件 | 調整内容 |
|---|---|
| ATR%ile ≥ 80（高ボラ） | SL乗数 ×1.2（ノイズで狩られないよう拡大） |
| ATR%ile ≤ 20（低ボラ） | SL乗数 ×0.8（スプレッド節約） |
| 直近勝率 < 40% | SL乗数 ×1.15（SL設定が狭すぎる可能性） |
| 直近勝率 > 65% | TP乗数 ×1.1（さらなる利益伸ばしを試みる） |
| 強トレンド（strong_bull/bear） | TP乗数 ×1.2（トレンドフォロー） |
| レンジ相場 | TP乗数 ×0.85（早めの利確） |

### ダッシュボード API

```
GET /dashboard/api/optimizer           # 最新の調整値と履歴10件
GET /dashboard/api/optimizer?n=30      # 履歴30件
```

### 調整履歴の確認（DB）

全ての調整結果は `param_history` テーブルに記録されます：

```sql
SELECT updated_at, atr_sl_mult, atr_tp_mult, regime, win_rate, reason
FROM   param_history
ORDER  BY id DESC
LIMIT  10;
```

---

## システム概要

| コンポーネント | 技術 | 役割 |
|---|---|---|
| 受信サーバー | Python / Flask | TradingViewアラートのWebhook受信 |
| AIエンジン | GPT-4o-mini (OpenAI) | シグナルの取引可否・期待値・注文タイプ判断 |
| 取引執行 | MetaTrader5 (Python) | XMTradingへの注文送信 |
| ニュースフィルター | MT5カレンダーAPI | 重要指標前後30分のエントリー禁止 |
| ポジション管理 | position_manager.py | 段階的利確＋BE移動＋トレーリング |
| ログDB | SQLite | 全判定・執行・負け分析の記録 |
| ダッシュボード | Flask + HTML/JS | ブラウザでの状態確認（15秒更新） |
| 通知 | LINE Notify | MT5切断・AI API障害のみ通知 |

---

## ファイル構成

```
trading_system/
├── app.py                 # メインエントリーポイント（Flask起動・初期化）
├── config.py              # 全設定の一元管理
├── database.py            # SQLite DB初期化・接続管理
├── validation.py          # シグナルバリデーション・正規化
├── signal_collector.py    # 受信バッファ（500ms収集窓）
├── batch_processor.py     # バッチ処理パイプライン
├── context_builder.py     # AIコンテキスト組み立て（MT5指標含む）
├── prompt_builder.py      # GPT-4o-miniプロンプト生成
├── ai_judge.py            # GPT-4o-mini API呼び出し
├── executor.py            # MT5注文執行モジュール（動的パラメータ統合）
├── market_hours.py        # 市場クローズ判定（XMサーバータイム）
├── news_filter.py         # ニュースフィルター（v2追加）
├── position_manager.py    # 段階的利確・BE・トレーリング（v2追加）
├── wait_buffer.py         # wait判定のバッファ管理
├── revaluator.py          # wait再評価エンジン
├── loss_analyzer.py       # 決済監視・負け分析（振り返りAI）
├── health_monitor.py      # MT5接続監視・自動再接続
├── notifier.py            # LINE通知
├── dashboard.py           # ブラウザダッシュボード（Blueprint）
├── logger_module.py       # ログ書き込み（SQLite連携）
├── backtester.py          # バックテストエンジン（v2.1追加）
├── param_optimizer.py     # ATR乗数動的最適化（v2.1追加）
├── tests/
│   ├── __init__.py
│   ├── test_risk_manager.py  # risk_manager.py ユニットテスト（v2.1追加）
│   └── test_executor.py      # executor.py ユニットテスト（v2.1追加）
├── requirements.txt       # Python依存ライブラリ
├── .env.example           # 環境変数テンプレート
├── .gitignore             # Git除外設定
└── trading_log.db         # SQLite DB（自動生成）
```

---

## セットアップ

### 初回起動時のコマンド（推奨）

```bash
cd trading_system
python -m pip install -r requirements.txt
python app.py
```

### 1. 依存ライブラリのインストール

```bash
cd trading_system
python -m pip install -r requirements.txt
```

### 2. 環境変数の設定

```bash
cp .env.example .env
# .env を編集して実際の値を入力
```

```env
MT5_LOGIN=12345678
MT5_PASSWORD=your_password
MT5_SERVER=XMTrading-MT5x
OPENAI_API_KEY=sk-...
LINE_NOTIFY_TOKEN=your_token
FLASK_PORT=5000
```

> ⚠️ `.env` は絶対に Git にコミットしないこと

### 3. 起動

```bash
cd trading_system
python app.py
```

---

## エンドポイント

| URL | メソッド | 説明 |
|---|---|---|
| `/webhook` | POST | TradingViewアラート受信 |
| `/health` | GET | MT5接続死活確認（200=OK, 503=切断） |
| `/dashboard` | GET | ブラウザダッシュボード（15秒自動更新） |
| `/dashboard/api/status` | GET | MT5状態・口座・直近シグナル |
| `/dashboard/api/loss_analysis` | GET | 負けトレード一覧 |
| `/dashboard/api/prompt_hints` | GET | プロンプト改善ヒントTOP10 |
| `/dashboard/api/stats` | GET | 勝率・PnL・期間別集計 |

---

## Webhookシグナル形式

```json
{
  "time":        "2026-02-23T13:35:00Z",
  "symbol":      "GOLD",
  "price":       "5153.225",
  "source":      "Lorentzian",
  "side":        "buy",
  "strength":    "strong",
  "comment":     "High Confidence Buy",
  "tf":          "5",
  "signal_type": "entry_trigger",
  "event":       "prediction_signal",
  "confirmed":   "bar_close"
}
```

---

## ポジション管理（v2）

1. **ブレークイーブン**: 含み益 ATR×1.0 到達時にSLをエントリー+2pipsへ移動
2. **第1TP（50%部分決済）**: 含み益 ATR×1.5 到達時に50%を確定
3. **トレーリングストップ**: 部分決済後、SL = 最高値 - ATR×1.0 で追跡

---

## デモ口座での検証

```python
# config.py のリスク設定を下げてテスト
"risk_percent": 0.1   # 0.1%でテスト → 確認後 1.0% に戻す
```

---

## 注意事項

- XMTradingのシンボル名は `XAUUSD` または `GOLD.m` など環境により異なる。`mt5.symbols_get()` で確認すること
- `order_filling` タイプはデモ口座で `IOC` が使えるか確認すること
- MT5カレンダーAPI` はMT5のバージョンや接続状況により取得できない場合がある（取得失敗時はフィルタースキップ）
