"""
データアクセス層 — protocol v1.0 (3-pool revision)
pending / submitted / fixed の物理分離を隠蔽し、サーバー・トレーナーに統一インターフェースを提供する。

不変条件:
  - fixed/ は read-only（API では絶対に書き込まない）
  - pending/labels/ の seed は **学習に含めない**
  - PUT /submit/{id}:
      pending   → submitted/ へ画像とマスクを物理移動
      submitted → submitted/labels/ を上書き（再 submit）
      fixed     → 拒否（PoolReadOnlyError）
"""
import os
import shutil
import random
import logging

from config import (
    PENDING_IMAGES_DIR, PENDING_LABELS_DIR,
    SUBMITTED_IMAGES_DIR, SUBMITTED_LABELS_DIR,
    FIXED_IMAGES_DIR, FIXED_LABELS_DIR,
)

logger = logging.getLogger(__name__)


# ---- 例外 ------------------------------------------------------------

class ImageNotFoundError(LookupError):
    """指定 image_id がどのプールにも存在しない"""


class PoolReadOnlyError(PermissionError):
    """fixed プールへの書込み試行"""


# ---- ヘルパー --------------------------------------------------------

_IMAGE_EXTS = (".png", ".jpg", ".jpeg")
_LABEL_EXTS = (".png",)


def _list_files(directory: str, exts: tuple) -> list[str]:
    if not os.path.isdir(directory):
        return []
    return sorted(
        f for f in os.listdir(directory)
        if f.lower().endswith(exts) and not f.startswith(".")
    )


def _pool_dirs(pool: str) -> tuple[str, str]:
    """pool 名から (images_dir, labels_dir) を返す"""
    if pool == "pending":
        return PENDING_IMAGES_DIR, PENDING_LABELS_DIR
    if pool == "submitted":
        return SUBMITTED_IMAGES_DIR, SUBMITTED_LABELS_DIR
    if pool == "fixed":
        return FIXED_IMAGES_DIR, FIXED_LABELS_DIR
    raise ValueError(f"unknown pool: {pool!r}")


def _label_filename(image_id: str) -> str:
    """画像ファイル名から label ファイル名を導く（拡張子は常に .png）"""
    base, _ = os.path.splitext(image_id)
    return f"{base}.png"


# ---- DataManager -----------------------------------------------------

class DataManager:
    """3 プール構造の統合データアクセス層"""

    POOLS = ("pending", "submitted", "fixed")

    # ----- 一覧 -----

    def list_pool_images(self, pool: str) -> list[str]:
        """指定プールの画像ファイル名一覧（昇順）"""
        images_dir, _ = _pool_dirs(pool)
        return _list_files(images_dir, _IMAGE_EXTS)

    # ----- 解決 -----

    def find_pool(self, image_id: str) -> str | None:
        """image_id がどのプールにあるかを返す。submitted → fixed → pending の順で探す。"""
        for pool in ("submitted", "fixed", "pending"):
            images_dir, _ = _pool_dirs(pool)
            if os.path.exists(os.path.join(images_dir, image_id)):
                return pool
        return None

    def get_image_path(self, image_id: str) -> str | None:
        pool = self.find_pool(image_id)
        if pool is None:
            return None
        images_dir, _ = _pool_dirs(pool)
        return os.path.join(images_dir, image_id)

    def get_label_path(self, image_id: str) -> str | None:
        """確定 label を submitted → fixed → pending の優先順で検索"""
        label_name = _label_filename(image_id)
        for pool in ("submitted", "fixed", "pending"):
            _, labels_dir = _pool_dirs(pool)
            path = os.path.join(labels_dir, label_name)
            if os.path.exists(path):
                return path
        return None

    def has_seed(self, image_id: str) -> bool:
        """pending プールに seed label が物理存在するか"""
        label_name = _label_filename(image_id)
        return os.path.exists(os.path.join(PENDING_LABELS_DIR, label_name))

    def has_annotation(self, image_id: str) -> bool:
        """submitted または fixed に label が存在するか"""
        label_name = _label_filename(image_id)
        return (
            os.path.exists(os.path.join(SUBMITTED_LABELS_DIR, label_name))
            or os.path.exists(os.path.join(FIXED_LABELS_DIR, label_name))
        )

    # ----- 書込み -----

    def submit(self, image_id: str, mask_data: bytes) -> tuple[str, str]:
        """マスク提出。元プールに応じて挙動が分岐する。

        Returns:
            (status, pool): status は "saved"（pending→submitted 新規）または
                            "updated"（submitted 上書き）。pool は常に "submitted"。

        Raises:
            ImageNotFoundError: image_id がどのプールにもない
            PoolReadOnlyError:  fixed プールへの提出
        """
        pool = self.find_pool(image_id)
        if pool is None:
            raise ImageNotFoundError(f"image '{image_id}' not found in any pool")
        if pool == "fixed":
            raise PoolReadOnlyError("fixed pool is read-only")

        label_name = _label_filename(image_id)
        target_label = os.path.join(SUBMITTED_LABELS_DIR, label_name)

        if pool == "pending":
            # pending → submitted へ画像を物理移動。pending 側の seed は破棄。
            src_image = os.path.join(PENDING_IMAGES_DIR, image_id)
            dst_image = os.path.join(SUBMITTED_IMAGES_DIR, image_id)
            shutil.move(src_image, dst_image)

            seed_path = os.path.join(PENDING_LABELS_DIR, label_name)
            if os.path.exists(seed_path):
                os.remove(seed_path)

            with open(target_label, "wb") as f:
                f.write(mask_data)
            logger.info(f"submitted (new): {image_id} ({len(mask_data)} bytes)")
            return ("saved", "submitted")

        # submitted の場合は label のみ上書き（再 submit）
        with open(target_label, "wb") as f:
            f.write(mask_data)
        logger.info(f"submitted (update): {image_id} ({len(mask_data)} bytes)")
        return ("updated", "submitted")

    # ----- アクティブラーニング -----

    def get_next_pending(self, strategy: str = "random") -> str | None:
        """pending プールから次の HITL 対象を返す。pending が空なら None。"""
        items = self.list_pool_images("pending")
        if not items:
            return None
        if strategy == "sequential":
            return items[0]
        return random.choice(items)

    # ----- 学習 -----

    def get_all_training_pairs(self) -> list[tuple[str, str]]:
        """学習用 (image_path, label_path) ペア。submitted ∪ fixed のみ。pending は含めない。"""
        pairs: list[tuple[str, str]] = []
        for pool in ("submitted", "fixed"):
            images_dir, labels_dir = _pool_dirs(pool)
            label_set = set(_list_files(labels_dir, _LABEL_EXTS))
            for fname in _list_files(images_dir, _IMAGE_EXTS):
                label_name = _label_filename(fname)
                if label_name in label_set:
                    pairs.append((
                        os.path.join(images_dir, fname),
                        os.path.join(labels_dir, label_name),
                    ))
        logger.info(f"training pairs: total={len(pairs)} (submitted ∪ fixed)")
        return pairs

    # ----- 統計 -----

    def get_stats(self) -> dict:
        """3 プールそれぞれの画像枚数とラベル枚数"""
        return {
            "pending": len(self.list_pool_images("pending")),
            "submitted": len(self.list_pool_images("submitted")),
            "fixed": len(self.list_pool_images("fixed")),
            "submitted_labels": len(_list_files(SUBMITTED_LABELS_DIR, _LABEL_EXTS)),
            "fixed_labels": len(_list_files(FIXED_LABELS_DIR, _LABEL_EXTS)),
            "pending_seeds": len(_list_files(PENDING_LABELS_DIR, _LABEL_EXTS)),
        }
