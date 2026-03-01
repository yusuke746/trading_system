# AI Trading System v3.5

TradingViewアラート × LLM構造化 × スコアリングエンジン × MT5
XMTrading / XAUUSD 専用 完全自動取引システム

---

## 🔰 このプログラムを一言で言うと

> **TradingViewが「チャンスかも」と叫んだら、LLMがデータを構造化し、ルールエンジンが「本当に勝てるか」をスコアで判定し、承認されたら自動でゴールド（金）を売買するシステムです。**

---

## 📖 わかりやすい説明

### 全体の流れ（6ステップ）

```
① TradingViewアラート受信
        ↓
② 500msバッファに溜める（まとめて処理）
        ↓
③ ルールベース（または実験的LLM）で生データを正規化JSONに構造化
        ↓
④ スコアリングエンジンが数値ルールで approve/wait/reject を判定
        ↓
⑤ approve → MT5でゴールドを売買
        ↓
⑥ ポジション管理（利確・損切り・トレーリング）
```

毎週日曜 UTC 20:00 に MetaOptimizer がバックグラウンドで自動実行され、SCORING_CONFIG を安全ガード付きで自動調整します。

### v3.0 → v3.5 の変更点

| v3.0 | v3.5 |
|---|---|
| `structurize()` は常にLLMを呼び出す | デフォルトは**ルールベース**、`LLM_STRUCTURIZE=1` のときのみ LLM を使用 |
| `APPROVE_THRESHOLD` をモジュールロード時に読み込む | `calculate_score()` 内で毎回 config から読み直す（動的更新に対応） |
| パラメータ調整は手動 | **MetaOptimizer** が週次自動最適化（安全ガード3条件付き） |

### v2.0 → v3.0 の根本的な変更点

| v2.0 | v3.0 |
|---|---|
| GPT-4o-mini が「approve/reject」を直接判定 | LLM は**データ構造化のみ**。判定は行わない |
| プロンプト次第で出力が揺らぐ | 判定を **Python の if/else ルール**で固定化 |
| 閾値調整が困難 | 全閾値を `SCORING_CONFIG` に集約し、バックテストで最適化可能 |
| バックテストとライブの乖離あり | `backtester_live.py` が**ライブと同一コードでシミュレーション** |

### 各パーツのやさしい説明

| ファイル | 何をするところ？ |
|---|---|
| `app.py` | **司令塔**。全体を起動・管理する |
| `signal_collector.py` | **受付係**。TradingViewの合図を500ms待って1つにまとめる |
| `batch_processor.py` | **コーディネーター**。まとめた合図をパイプラインに渡す段取りをする |
| `context_builder.py` | **情報収集係**。MT5から相場の状況データを集めてLLMに渡す |
| `prompt_builder.py` | **翻訳係**。相場データをLLMが読めるプロンプトに変換する |
| `llm_structurer.py` | **構造化係**。デフォルトはルールベースで高速・確定的に正規化JSON生成。`LLM_STRUCTURIZE=1` でLLM実験モードに切替可能 |
| `scoring_engine.py` | **審査員**。構造化データを受け取り、数値ルールで approve/wait/reject を判定する（config を毎回読み直して動的更新に対応） |
| `ai_judge.py` | **パイプライン窓口**。llm_structurer → scoring_engine を呼び出し、旧形式で結果を返す |
| `meta_optimizer.py` | **自動調整係**。毎週日曜UTC20:00にDBを分析し、安全ガード付きで SCORING_CONFIG を自動最適化する |
| `executor.py` | **注文係**。approveが出たら実際にMT5で注文を出す |
| `position_manager.py` | **管理係**。ポジションをずっと監視して利確・損切りを自動管理する |
| `risk_manager.py` | **リスク番人**。1日の損失上限・連続負け・週明けギャップを監視する |
| `news_filter.py` | **ニュース番人**。重要指標発表の前後30分はエントリーをブロックする |
| `revaluator.py` | **再審査係**。waitと判定されたシグナルを定期的に再評価する |
| `loss_analyzer.py` | **振り返り係**。損切りが出るたびにAIに「なぜ負けたか」を分析させる |
| `health_monitor.py` | **見張り役**。MT5接続が切れたら自動で再接続してLINEに通知する |
| `dashboard.py` | **ダッシュボード**。ブラウザで状態・損益・分析をリアルタイム確認できる |
| `backtester_live.py` | **ライブ型バックテスター**。実際のTVアラート履歴 + ライブ同一ロジックで検証する |
| `backtester.py` | **ATR戦略バックテスター**。ATRブレークアウト/RSI戦略のシミュレーション |
| `param_optimizer.py` | **パラメータ最適化**。市場環境・勝率に基づきATR乗数を動的調整する |
| `download_ohlcv.py` | **データ取得係**。yfinanceからOHLCVデータをCSVにダウンロードする |

### ポジション管理の3ステップ（わかりやすく）

```
エントリー後
  ┌──────────────────────────────────────────────────┐
  │ STEP1: 含み益がATR×1.5になったら                  │
  │        → ストップロスをエントリー価格付近に移動   │
  │          （もう絶対に損しない状態＝ブレークイーブン）│
  ├──────────────────────────────────────────────────┤
  │ STEP2: 含み益がATR×2.5になったら                  │
  │        → ポジションの50%を利益確定                │
  │          （確実に利益を取る）                      │
  ├──────────────────────────────────────────────────┤
  │ STEP3: 残り50%はトレーリングストップで追いかける   │
  │        → 高値更新のたびにSLを引き上げる            │
  │          （利益を最大限に伸ばす）                  │
  └──────────────────────────────────────────────────┘
```

### 多重安全装置

このシステムには注文を出す前に **6つのガード** があります：

1. 🗞 **ニュースフィルター** — 重要経済指標の前後30分は一切エントリーしない
2. 🕐 **市場時間チェック** — デイリーブレイク（23:45〜1:00 UTC）はエントリーしない
3. 📉 **当日損失制限** — 口座残高の5%を超えたら自動停止
4. 🔴 **連続損失制限** — 3連敗したら自動停止
5. 📊 **ギャップリスク** — 月曜早朝の週明けギャップが $15 以上なら停止
6. 💼 **複数ポジション管理** — 最大5ポジション同時保有（合計リスク上限 10%）

---

## 📊 システム評価

### 総合評価: ★★★★☆ (4.2 / 5.0)

---

### ✅ 強み

| 項目 | 評価 | 詳細 |
|---|---|---|
| アーキテクチャ設計 | ★★★★★ | 責務が明確に分離されており、各モジュールが独立して動作する。テストや改修がしやすい。 |
| リスク管理の多層性 | ★★★★★ | ニュースフィルター・当日損失上限・連続損失・ギャップリスクと4層の安全装置を持つ。 |
| AI活用設計（v3.0） | ★★★★★ | LLMを「構造化」専任にし、判定を数値ルールに分離。バックテストと本番で同一コードが動く。 |
| バックテスト精度 | ★★★★★ | `backtester_live.py` で実TVアラート + ライブ同一ロジックにより乖離を構造的に排除。 |
| ポジション管理 | ★★★★☆ | BE移動→部分決済→トレーリングの3ステップ管理は実践的で損益改善に直結する。 |
| エラー耐性 | ★★★★☆ | MT5未接続時のテストモード、APIエラー時のフォールバック、バッファの差し戻しなど障害時の継続性が考慮されている。 |
| 観測性（ダッシュボード・ログ） | ★★★★☆ | SQLiteへの全履歴記録 + ブラウザダッシュボードにより稼働状況をリアルタイムで把握できる。 |

---

### ⚠️ 改善余地

| 項目 | 評価 | 詳細 |
|---|---|---|
| XAUUSD専用設計 | ★★★☆☆ | シンボルや計算式がゴールド専用にハードコードされており、他の通貨ペアへの転用が難しい。 |
| AIプロンプトの固定化 | ★★★☆☆ | `llm_structurer.py` の構造化プロンプトが固定のため、市場環境の変化に対して手動更新が必要。 |
| yfinanceデータの精度 | ★★★☆☆ | `download_ohlcv.py` で取得したデータはMT5の実際のスプレッドを含まない。 |

---

### 📈 各領域スコア

```
設計・可読性    ████████████████████  90点
リスク管理      ████████████████████  85点
AI活用度        ████████████████████  90点（v3.5で維持）
ポジション管理  ████████████████      80点
テスト・品質    ██████████████████    75点（157件に拡充）
汎用性          ████████████          60点
─────────────────────────────────────
平均            ████████████████████  80点 / 100点
実用的総合評価  ████████████████████  84点 / 100点（★★★★☆）
```

---

### 🎯 バージョン履歴

**v2.1 で追加された機能：**
1. ✅ **ユニットテストの追加**（`risk_manager.py`・`executor.py` で49テスト）
2. ✅ **バックテスト機能の実装**（`backtester.py`）
3. ✅ **パラメータの動的最適化**（`param_optimizer.py`）

**v2.2 で実施された改善：**
1. ✅ **バックテストにAI判断を組み込む**（`AiJudgeMock`・`use_ai_mock`フラグ・CLIオプション）
2. ✅ **SQLite接続のコネクションプール化**（`ConnectionPool`・スレッドローカル再利用）
3. ✅ **ドキュメントとconfigの整合性修正**（README更新・リスク設定根拠テーブル追加）
4. ✅ **ニュースフィルターのフェイルセーフ**（取得失敗時にブロック・`fail_safe_triggered`キー）
5. ✅ **日次損失上限を-10%→-5%に見直し**（残高比率判定・フォールバック残高をconfigから取得）

**v3.5 で実施された変更：**
1. ✅ **`structurize()` をルールベースデフォルトに変更**（`LLM_STRUCTURIZE=1` のときのみ LLM 使用・APIコストゼロ化）
2. ✅ **`calculate_score()` の閾値を動的読み込みに修正**（モジュールロード時の固定値バグを解消・MetaOptimizer による動的更新に対応）
3. ✅ **`meta_optimizer.py` 新規追加**（毎週日曜UTC20:00に自動実行・安全ガード3条件・バックテスト検証付き）
4. ✅ **`app.py` に MetaOptimizer 起動を追加**（startup() でバックグラウンドスケジューラ起動）
5. ✅ **ユニットテスト 146件 → 157件に拡充**（`test_meta_optimizer.py` 11件追加）

**v3.0 で実施された変更：**
1. ✅ **LLMの役割を「構造化専任」に変更**（`llm_structurer.py` 新規追加）
2. ✅ **数値ルールベーススコアリングエンジン実装**（`scoring_engine.py` 新規追加）
3. ✅ **ai_judge.py を v3.0 パイプラインに対応**（後方互換性維持）
4. ✅ **ライブ型バックテスター実装**（`backtester_live.py` 新規追加）
5. ✅ **OHLCVデータ取得スクリプト追加**（`download_ohlcv.py` 新規追加）
6. ✅ **SCORING_CONFIG・SESSION_SLTP_ADJUSTをconfigに集約**
7. ✅ **ユニットテスト 77件 → 146件に拡充**（`test_ai_judge_v3.py`・`test_llm_structurer.py`・`test_scoring_engine.py` 追加）

---

## 🧪 ユニットテスト

### 実行方法

```bash
cd trading_system
python -m pytest tests/ -v
```

### テスト一覧（157件）

| ファイル | テストクラス | 件数 | 内容 |
|---|---|---|---|
| `tests/test_risk_manager.py` | TestCheckDailyLossLimit | 7件 | 当日損失上限チェック（残高比率判定・正常・超過・DB誤り） |
| `tests/test_risk_manager.py` | TestCheckConsecutiveLosses | 12件 | 連続損失チェック（各パターン・時間ベースリセット） |
| `tests/test_risk_manager.py` | TestCheckGapRisk | 5件 | 週明けギャップチェック（月曜・他曜日・MT5なし） |
| `tests/test_risk_manager.py` | TestRunAllRiskChecks | 6件 | 統合チェック（各ブロック条件・優先順位） |
| `tests/test_executor.py` | TestBuildOrderParams | 18件 | SL/TP計算・ロット計算・ATRフィルター・指値注文 |
| `tests/test_executor.py` | TestPreExecutionCheck | 6件 | 執行前チェック順序・各ブロック条件 |
| `tests/test_backtester.py` | TestAiJudgeMock | 6件 | AiJudgeMock の approve/reject 確率検証 |
| `tests/test_backtester.py` | TestBacktestEngineAiMock | 6件 | use_ai_mock フラグの動作・トレード数・summary |
| `tests/test_database.py` | TestConnectionPool | 6件 | スレッドローカル接続・同一スレッド再利用・別スレッド分離 |
| `tests/test_database.py` | TestModuleGetConnection | 2件 | モジュールレベルの get_connection() 動作確認 |
| `tests/test_news_filter.py` | TestNewsFilterFailSafe | 5件 | MT5未インストール時の fail_safe 動作 |
| `tests/test_news_filter.py` | TestNewsFilterApiError | 2件 | カレンダーAPI取得失敗時の fail_safe 動作 |
| `tests/test_news_filter.py` | TestNewsFilterReturnStructure | 1件 | 全ケースで fail_safe_triggered キー存在確認 |
| `tests/test_llm_structurer.py` | TestFallbackStructurize | 16件 | ルールベースフォールバックの構造化ロジック検証 |
| `tests/test_llm_structurer.py` | TestValidateAndFixSchema | 5件 | スキーマバリデーション・デフォルト補完 |
| `tests/test_llm_structurer.py` | TestSafeFloat | 3件 | _safe_float() のエッジケース（None・文字列・NaN） |
| `tests/test_scoring_engine.py` | TestInstantReject | 3件 | 即rejectパターン（レンジ中央・データ欠損） |
| `tests/test_scoring_engine.py` | TestRegimeScore | 3件 | レジーム別基礎点（trend/breakout/range） |
| `tests/test_scoring_engine.py` | TestStructureScore | 5件 | ゾーン・FVG・流動性スイープの加点ルール |
| `tests/test_scoring_engine.py` | TestMomentumScore | 3件 | トレンド整合・RSIダイバージェンス |
| `tests/test_scoring_engine.py` | TestSignalQualityScore | 5件 | bar_close確認・セッション補正・TV信頼度 |
| `tests/test_scoring_engine.py` | TestDecisionThresholds | 4件 | approve/wait/reject 閾値判定 |
| `tests/test_scoring_engine.py` | TestPatternSimilarity | 4件 | Lorentzian v2 pattern_similarity スコア |
| `tests/test_ai_judge_v3.py` | TestAskAiV3 | 4件 | v3.0パイプライン（LLM構造化 → スコアリング） |
| `tests/test_ai_judge_v3.py` | TestShouldExecute | 5件 | should_execute() の判定ロジック |
| `tests/test_ai_judge_v3.py` | TestScoreToConfidence | 4件 | _score_to_confidence() のマッピング |
| `tests/test_ai_judge_v3.py` | TestErrorHandling | 1件 | エラー時の reject フォールバック |
| `tests/test_meta_optimizer.py` | TestSafetyCheck | 7件 | 安全ガードのパラメータバリデーション（変更幅・範囲・閾値制限） |
| `tests/test_meta_optimizer.py` | TestApplyConfig | 2件 | config.py の原子的書き換え検証 |
| `tests/test_meta_optimizer.py` | TestTunableParams | 2件 | TUNABLE_PARAMS の SCORING_CONFIG 整合性チェック |

---

## 🏗 スコアリングエンジン（v3.5）

### アーキテクチャ

```
生コンテキストデータ (context_builder.py)
        ↓
llm_structurer.py (デフォルト: ルールベース / LLM_STRUCTURIZE=1 のときのみ LLM)
        ↓ 正規化JSON
scoring_engine.py (Python if/else ルール / config を毎回読み直して動的更新対応)
        ↓
{ decision: approve/wait/reject, score: float, score_breakdown: {...} }
        ↓ 週次バックグラウンド
meta_optimizer.py (毎週日曜UTC20:00 / 安全ガード付き SCORING_CONFIG 自動更新)
```

### スコア加減点ルール（SCORING_CONFIG）

| 条件 | 加減点 |
|---|---|
| レジーム: trend | +0.15 |
| レジーム: breakout | +0.20 |
| レジーム: range | -0.10 |
| ゾーンタッチ（方向一致） | +0.20 |
| FVGタッチ（方向一致） | +0.15 |
| 流動性スイープ | +0.25 |
| スイープ+ゾーン combo | +0.10 |
| トレンド整合 | +0.10 |
| RSI確認（方向一致） | +0.05 |
| RSIダイバージェンス | -0.20 |
| bar_close確認済み | +0.10 |
| London/NYセッション | +0.05 |
| 東京セッション | -0.05 |
| オフアワーズ | -0.15 |
| TV信頼度 > 0.65 | +0.10 |
| TV信頼度 < 0.40 | -0.10 |
| pattern_similarity > 0.70 | +0.10 |
| pattern_similarity < 0.30 | -0.10 |

### 判定閾値

| 判定 | スコア |
|---|---|
| approve | ≥ 0.45 |
| wait | ≥ 0.10 |
| reject | < 0.10 |

### 即reject（Gate）パターン

| 条件 | 説明 |
|---|---|
| レンジ中央での順張り | SMA20乖離±0.3%以内・ゾーン/FVGタッチなし |
| 重要データ欠損 | rsi_value / adx_value / atr_expanding が不足 |
| Gate2 | Q-trend不一致 かつ bar_close未確認（Q-trendデータあり時のみ） |

---

## 📈 ライブ型バックテスト（backtester_live.py）

### 既存 backtester.py との違い

| 項目 | backtester.py | backtester_live.py |
|---|---|---|
| シグナル源 | ATRブレークアウト/RSI逆張りで自動生成 | **実際のTVアラート履歴CSVをそのまま入力** |
| フィルタリング | 独自ロジック | **scoring_engine（ライブと同一コード）** |
| バックテスト乖離 | あり（別戦略） | **構造的に排除** |

### CLIの使い方

```bash
# TradingViewアラート履歴CSV + OHLCVのCSVを両方指定
python backtester_live.py --alerts tv_alerts.csv --ohlcv ohlcv.csv

# スコアリング閾値を変えて比較
python backtester_live.py --alerts tv_alerts.csv --ohlcv ohlcv.csv \
    --approve-threshold 0.45 --wait-threshold 0.15

# Gate2を無効にして効果を測定
python backtester_live.py --alerts tv_alerts.csv --ohlcv ohlcv.csv --no-gate2

# 閾値感度分析
python backtester_live.py --alerts tv_alerts.csv --ohlcv ohlcv.csv --sensitivity

# MT5から直接OHLCVを取得
python backtester_live.py --alerts tv_alerts.csv --mt5 --symbol GOLD --tf M5

# 結果CSVに出力
python backtester_live.py --alerts tv_alerts.csv --ohlcv ohlcv.csv --output result.csv
```

### CLIオプション一覧

| オプション | デフォルト | 説明 |
|---|---|---|
| `--alerts` | （必須） | TradingViewアラート履歴CSV |
| `--ohlcv` | — | OHLCVデータCSV |
| `--mt5` | False | MT5から直接OHLCV取得 |
| `--symbol` | GOLD | シンボル |
| `--tf` | M5 | 時間足 |
| `--bars` | 5000 | MT5取得バー数 |
| `--approve-threshold` | config値 | approveスコア閾値 |
| `--wait-threshold` | config値 | waitスコア閾値 |
| `--no-gate2` | False | Gate2無効化 |
| `--sl-mult` | config値 | ATR SL乗数上書き |
| `--tp-mult` | config値 | ATR TP乗数上書き |
| `--spread` | 0.50 | スプレッド $ |
| `--slippage` | 0.10 | スリッページ $ |
| `--initial-balance` | 10000 | 初期残高 |
| `--risk-pct` | config値 | 1トレードのリスク% |
| `--sensitivity` | False | approve_threshold感度分析 |
| `--output` | — | 結果CSV出力先 |

### 入力CSV形式（TradingViewアラート履歴エクスポート）

```
Alert ID, Ticker, Name, Description, Time, Webhook status
```

`Description` 列はJSON文字列（`time` / `price` / `source` / `side` / `signal_type` / `event` / `confirmed` / `confidence` / `strength` / `action` など）。

---

## 📉 ATR戦略バックテスト（backtester.py）

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

## 📥 OHLCVデータ取得（download_ohlcv.py）

MT5 や口座なしでバックテスト用の OHLCV データを yfinance から取得します。

### 使い方

```bash
python download_ohlcv.py                       # GOLD 5m 60日分（デフォルト）
python download_ohlcv.py --tf 15m              # 15分足
python download_ohlcv.py --tf 1h --days 730    # 1時間足 2年分
python download_ohlcv.py --symbol "EURUSD=X"   # FX ペア
```

### 対応シンボル（主要）

| MT5シンボル | yfinanceティッカー |
|---|---|
| GOLD / XAUUSD | GC=F |
| SILVER | SI=F |
| EURUSD | EURUSD=X |
| USDJPY | JPY=X |
| BTCUSD | BTC-USD |

### 時間足と取得可能期間

| 時間足 | 最大取得期間 |
|---|---|
| 1m | 7日 |
| 5m / 15m / 30m | 60日 |
| 1h | 730日（2年） |
| 1d | 制限なし |

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

## ⚙️ リスク設定値の根拠

| 設定項目 | 値 | 根拠 |
|---|---|---|
| max_daily_loss_percent | -5.0% | risk_percent=2%で約2.5連敗分に相当。一般的なリスク管理基準（5%）に準拠 |
| max_consecutive_losses | 3 | 3連敗はドローダウン約6%。戦略不調の早期検知 |
| consecutive_loss_reset_hours | 24h | 24時間経過した古い損失は連続カウントから除外。翌日は新鮮な状態でトレード開始可能（0で無効化） |
| gap_block_threshold_usd | $15 | GOLDの平均スプレッド+スリッページの約3倍を閾値とする |
| max_positions | 5 | 最大同時リスクは max_total_risk_percent=10% で上限管理 |
| approve_threshold | 0.45 | デモ検証後に調整可。SCORING_CONFIG で管理 |
| wait_threshold | 0.10 | approve未満・これ以上でwait判定 |

---

## システム概要

| コンポーネント | 技術 | 役割 |
|---|---|---|
| 受信サーバー | Python / Flask | TradingViewアラートのWebhook受信 |
| LLM構造化エンジン | GPT-4o-mini (OpenAI) | 生データを正規化JSONに変換（デフォルト無効・`LLM_STRUCTURIZE=1` で有効化） |
| スコアリングエンジン | Python ルールベース | 構造化データから approve/wait/reject を数値判定（config 動的読み込み） |
| 週次最適化 | MetaOptimizer | 毎週日曜UTC20:00に SCORING_CONFIG を安全ガード付きで自動調整 |
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
├── config.py              # 全設定の一元管理（SCORING_CONFIG / SESSION_SLTP_ADJUST含む）
├── database.py            # SQLite DB初期化・接続管理（ConnectionPool）
├── validation.py          # シグナルバリデーション・正規化
├── signal_collector.py    # 受信バッファ（500ms収集窓）
├── batch_processor.py     # バッチ処理パイプライン
├── context_builder.py     # AIコンテキスト組み立て（MT5指標含む）
├── prompt_builder.py      # LLM向けプロンプト生成
├── llm_structurer.py      # LLMによるデータ構造化（v3.0追加・v3.5でルールベースデフォルト化）
├── scoring_engine.py      # 数値ルールベーススコアリング（v3.0追加・v3.5で動的config読み込み対応）
├── ai_judge.py            # AI判定パイプライン窓口（v3.0: 後方互換維持）
├── meta_optimizer.py      # 週次自動パラメータ最適化エンジン（v3.5追加）
├── executor.py            # MT5注文執行モジュール（動的パラメータ統合）
├── market_hours.py        # 市場クローズ判定（XMサーバータイム）
├── news_filter.py         # ニュースフィルター
├── position_manager.py    # 段階的利確・BE・トレーリング
├── wait_buffer.py         # wait判定のバッファ管理
├── revaluator.py          # wait再評価エンジン
├── loss_analyzer.py       # 決済監視・負け分析（振り返りAI）
├── health_monitor.py      # MT5接続監視・自動再接続
├── notifier.py            # LINE通知
├── dashboard.py           # ブラウザダッシュボード（Blueprint）
├── logger_module.py       # ログ書き込み（SQLite連携）
├── backtester.py          # ATR戦略バックテストエンジン
├── backtester_live.py     # ライブ型バックテスト（TVアラート+ライブ同一コード）（v3.0追加）
├── param_optimizer.py     # ATR乗数動的最適化
├── download_ohlcv.py      # yfinance OHLCVデータ取得（v3.0追加）
├── tests/
│   ├── __init__.py
│   ├── test_risk_manager.py      # risk_manager.py ユニットテスト（30件）
│   ├── test_executor.py          # executor.py ユニットテスト（24件）
│   ├── test_backtester.py        # backtester.py ユニットテスト（12件）
│   ├── test_database.py          # database.py ユニットテスト（8件）
│   ├── test_news_filter.py       # news_filter.py ユニットテスト（8件）
│   ├── test_llm_structurer.py    # llm_structurer.py ユニットテスト（24件）（v3.0追加）
│   ├── test_scoring_engine.py    # scoring_engine.py ユニットテスト（26件）（v3.0追加）
│   ├── test_ai_judge_v3.py       # ai_judge.py v3.0 ユニットテスト（14件）（v3.0追加）
│   └── test_meta_optimizer.py    # meta_optimizer.py ユニットテスト（11件）（v3.5追加）
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
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...  # MetaOptimizer 通知用（任意）
LLM_STRUCTURIZE=0  # 1 にすると structurize() が LLM を使用（デフォルト: 0=ルールベース）
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
| `/dashboard/api/backtest` | GET | バックテスト実行（`?bars=1000&sl_mult=2.0&tp_mult=3.0&strategy=breakout`） |
| `/dashboard/api/optimizer` | GET | パラメータ最適化結果（`?n=30`で履歴件数指定） |

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

1. **ブレークイーブン**: 含み益 ATR×1.5 到達時にSLをエントリー+2pipsへ移動
2. **第1TP（50%部分決済）**: 含み益 ATR×2.5 到達時に50%を確定
3. **トレーリングストップ**: 部分決済後、SL = 最高値 - ATR×2.0 で追跡

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
- MT5カレンダーAPI はMT5のバージョンや接続状況により取得できない場合がある（取得失敗時はフェイルセーフでエントリーブロック）
- `download_ohlcv.py` で取得したデータはMT5の実際のスプレッドを含まない。バックテスト時は `--spread` オプションで手動指定すること
