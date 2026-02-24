# AI Trading System v2.0

TradingViewアラート × GPT-4o-mini × MT5  
XMTrading / XAUUSD 専用 完全自動取引システム

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
├── executor.py            # MT5注文執行モジュール
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
- `MT5カレンダーAPI` はMT5のバージョンや接続状況により取得できない場合がある（取得失敗時はフィルタースキップ）
