# aiortc ジッタバッファ修正 PR 指示書

## 概要

aiortc の `jitterbuffer.py` にパケットロス時の致命的なバグがある。
1パケット欠損で映像が永久に破損し回復しない。修正PRを作成する。

## バグの詳細

### Bug #1: `_remove_frame()` が1パケット欠損で全停止

**ファイル:** `src/aiortc/jitterbuffer.py`、`_remove_frame()` メソッド

```python
if packet is None:
    break  # ← ここが問題
```

`_origin` から順にスキャンし、`None`スロット（欠損パケット）に遭遇すると即座に `break`。
`_origin` が進まないため、以降の全フレームが配信されなくなる。

### Bug #2: `smart_remove()` がフレーム境界を無視

バッファオーバーフロー時に `smart_remove()` が `_origin` をフレーム境界を無視して進める。
次の `_remove_frame()` がフレーム途中から組み立てを開始し、不完全なペイロードをデコーダーに渡す。

### Bug #3: フレーム完全性チェックがない

`_remove_frame()` の line 81 で `b"".join([x._data for x in packets])` する際、
RTPシーケンス番号の連続性チェックがない。欠損パケットがあっても残りのパケットを結合してしまう。

## 再現手順

1. WebRTCで映像ストリーミング中にパケットロスを発生させる（例: `tc qdisc add dev eth0 root netem loss 1%`）
2. 映像が一度乱れると、以降永久に回復しない
3. キーフレーム強制挿入、デコーダーリセット、PLI送信のいずれも効果なし

## 修正方針

### `_remove_frame()` の修正

`None`スロットに遭遇した場合:
1. 現在組み立て中のフレーム（不完全）を**破棄**する
2. `_origin` をギャップの先まで進める
3. 次のフレームからスキャン続行
4. PLIフラグを立てる（キーフレーム要求）

```python
# 修正のイメージ
if packet is None:
    if timestamp is not None:
        # 不完全フレームをスキップ
        # ギャップの終端を見つけて _origin を進める
        gap_end = count + 1
        while gap_end < self.capacity:
            gap_pos = (self._origin + gap_end) % self._capacity
            if self._packets[gap_pos] is not None:
                break
            gap_end += 1
        self.remove(gap_end)
        # 再スキャン（再帰的に次のフレームを探す）
        return self._remove_frame(sequence_number)
    else:
        break
```

### `smart_remove()` の修正（オプション）

フレーム境界（タイムスタンプの切り替わり）を尊重して `_origin` を進める。

### フレーム完全性チェック追加

`_remove_frame()` でフレームを組み立てる際、パケットのシーケンス番号が連続しているか検証。
不連続ならフレームを破棄。

## テスト

### 既存テストの確認

`tests/test_jitterbuffer.py` に既存テストがあるはず。まずこれを確認して理解する。

### 追加テスト

1. **パケットロスでのスキップ**: フレームAの中間パケットを欠損させ、フレームBが正常に配信されることを確認
2. **連続パケットロス**: 複数パケット連続欠損でも回復すること
3. **バッファオーバーフロー後の回復**: `smart_remove()` 後にフレーム境界が維持されること
4. **PLIフラグ**: パケットロス時にPLIフラグが立つこと

## PRのスコープ

- `src/aiortc/jitterbuffer.py` の修正
- `tests/test_jitterbuffer.py` のテスト追加
- 既存テストが全てパスすること
- CHANGELOG へのエントリ追加は不要（メンテナが行う）

## 注意事項

- 既存の動作を壊さないこと。`_remove_frame()` の正常系（パケットロスなし）は変更しない
- aiortc のコーディングスタイルに従うこと
- RTP marker bit の活用も検討（フレーム末尾の検出に使える）
- テストは `pytest` で実行: `pytest tests/test_jitterbuffer.py -v`

## 参考

- 詳細な分析: `../dlc_test/docs/adr/016-aiortc-jitterbuffer-bug.md`
- aiortc GitHub Issues: #26 (ジッタバッファ容量), #58 (PLI), #1359 (H264デコード失敗)
- Google "Handling Packet Loss in WebRTC": https://research.google.com/pubs/archive/41611.pdf
