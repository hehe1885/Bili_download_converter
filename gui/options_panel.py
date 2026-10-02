"""选项面板：把 :class:`m4sconverter.models.Options` 映射成一组控件。

面板不持有 ``Options`` 实例，只做双向搬运，便于随时从配置重新载入。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from m4sconverter.models import (
    AssOptions,
    ConflictMode,
    Engine,
    HashMode,
    LayoutMode,
    NameFilter,
    Options,
)
from m4sconverter.naming import TEMPLATE_FIELDS, layout_template

#: 常见中文字体，方便一键切换。
COMMON_FONTS = ["黑体", "微软雅黑", "思源黑体", "宋体", "楷体", "仿宋", "Arial", "Noto Sans CJK SC"]

CANVAS_PRESETS = [
    ("跟随视频分辨率（推荐）", "auto"),
    ("1920 x 1080（固定）", "1920x1080"),
    ("1280 x 720", "1280x720"),
    ("自定义…", "custom"),
]


def _hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet("color: #666; font-size: 11px;")
    return lab


def _setup_combo(combo: QComboBox) -> QComboBox:
    """限制下拉框的最小宽度。

    QComboBox 默认按*最长条目*决定最小宽度，选项文字一长就会把左栏顶宽，
    输入框被挤出可视区，看上去就是"只显示了一半"。这里改成按固定字符数
    估算，长条目通过悬停提示（tooltip）补充说明。
    """
    combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(6)
    return combo


class OptionsPanel(QWidget):
    """全部转换选项。"""

    #: 用户改了任何选项。
    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build()
        self.load_from(Options())

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll)

        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setSpacing(8)

        layout.addWidget(self._group_io())
        layout.addWidget(self._group_naming())
        layout.addWidget(self._group_ass())
        layout.addWidget(self._group_sidecar())
        layout.addWidget(self._group_hash())
        layout.addWidget(self._group_behavior())
        layout.addWidget(self._group_engine())
        layout.addStretch(1)

    # -- 输入输出 ------------------------------------------------------
    def _group_io(self) -> QGroupBox:
        box = QGroupBox("输入 / 输出")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignRight)

        self.import_edit = QLineEdit()
        self.import_edit.setPlaceholderText("B 站缓存目录（含 m4s 的目录）")
        form.addRow("导入目录", self._with_browse(self.import_edit, self._pick_import))

        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("留空 = <导入目录>\\output")
        form.addRow("输出目录", self._with_browse(self.output_edit, self._pick_output))

        self.per_import_check = QCheckBox("再按导入目录名分子文件夹")
        form.addRow("", self.per_import_check)
        return box

    # -- 命名 ----------------------------------------------------------
    def _group_naming(self) -> QGroupBox:
        box = QGroupBox("命名 / 布局")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignRight)

        self.layout_combo = QComboBox()
        for label, mode, tip in (
            ("平铺（推荐）", LayoutMode.FLAT, "output\\视频名.mp4 —— 全部平铺在输出目录"),
            ("按标题建文件夹", LayoutMode.TITLE_DIR, "output\\视频名\\视频名.mp4"),
            ("按 UP 建文件夹", LayoutMode.UP_DIR, "output\\UP主\\视频名.mp4"),
            ("按合集建文件夹", LayoutMode.ORIGINAL, "output\\合集-UP主\\分P.mp4 —— 一个合集一个文件夹"),
            ("自定义模板", LayoutMode.TEMPLATE, "用占位符自由拼路径与文件名"),
        ):
            self.layout_combo.addItem(label, mode)
            self.layout_combo.setItemData(self.layout_combo.count() - 1, tip, Qt.ToolTipRole)
        _setup_combo(self.layout_combo)
        self.layout_combo.currentIndexChanged.connect(self._on_layout_changed)
        form.addRow("布局", self.layout_combo)

        self.template_edit = QLineEdit()
        self.template_edit.setPlaceholderText("{title}.mp4")
        help_btn = QPushButton("占位符…")
        help_btn.setFixedWidth(84)
        help_btn.clicked.connect(self._show_template_help)
        form.addRow("命名模板", self._with_button(self.template_edit, help_btn))

        self.template_hint = _hint("")
        form.addRow("", self.template_hint)

        self.filter_combo = QComboBox()
        # 只有两种规则，标签一律用大白话——早先那三档（细粒度替换 / 替换+兜底 /
        # 只替换非法字符）用户的原话是"三种容易让别人看不懂"。
        for label, mode, tip in (
            ("替换（推荐）", NameFilter.STANDARD,
             "把 Windows 禁止的 < > : \" / \\ | ? * 换成长得像但合法的替身"),
            ("最小改动", NameFilter.MINIMAL,
             "只把 Windows 禁止的 \\ / : * ? \" < > | 换成 _，其余字符原样保留"),
        ):
            self.filter_combo.addItem(label, mode)
            self.filter_combo.setItemData(self.filter_combo.count() - 1, tip, Qt.ToolTipRole)
        _setup_combo(self.filter_combo)
        form.addRow("文件名清洗", self.filter_combo)

        # 就地解释两种规则在干什么。不能只靠悬停提示——用户不会主动去悬停，
        # 只会来问"这是什么意思"。两档都只碰 Windows 禁止的那 9 个字符
        # （< > : " / \ | ? *），区别只在换成什么：替身 vs 下划线。
        # 例：「示例视频 <上>」->「示例视频 《上》」（替换）
        #     「示例视频 <上>」->「示例视频 _上_」（最小改动）
        self.filter_hint = _hint(
            "替换：把文件名禁止的字符换成\"长得像但合法\"的替身\n"
            "最小改动：仅替换文件名禁止的字符为_"
        )
        form.addRow("", self.filter_hint)

        self.conflict_combo = QComboBox()
        for label, mode in (
            ("跳过（推荐）", ConflictMode.SKIP),
            ("覆盖", ConflictMode.OVERWRITE),
            ("自动改名", ConflictMode.RENAME),
            ("每个都询问", ConflictMode.ASK),
        ):
            self.conflict_combo.addItem(label, mode)
        _setup_combo(self.conflict_combo)
        form.addRow("同名冲突", self.conflict_combo)
        return box

    # -- 弹幕字幕 ------------------------------------------------------
    def _group_ass(self) -> QGroupBox:
        box = QGroupBox("弹幕字幕（ASS）")
        outer = QVBoxLayout(box)

        self.ass_check = QCheckBox("生成 ASS 弹幕字幕")
        self.ass_check.toggled.connect(self._on_ass_toggled)
        outer.addWidget(self.ass_check)

        detail = QWidget()
        self.ass_detail = detail
        form = QFormLayout(detail)
        form.setLabelAlignment(Qt.AlignRight)
        form.setContentsMargins(0, 0, 0, 0)

        self.ass_reuse_check = QCheckBox("复用已有弹幕文件")
        self.ass_reuse_check.setToolTip("缓存目录里已有 danmaku.ass 时直接复制，不重新生成")
        form.addRow("", self.ass_reuse_check)

        self.font_combo = QComboBox()
        self.font_combo.setEditable(True)
        self.font_combo.addItems(COMMON_FONTS)
        _setup_combo(self.font_combo)
        form.addRow("字体", self.font_combo)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 200)
        form.addRow("字号", self.font_size_spin)

        self.alpha_spin = QDoubleSpinBox()
        self.alpha_spin.setRange(0.0, 1.0)
        self.alpha_spin.setSingleStep(0.05)
        self.alpha_spin.setDecimals(2)
        form.addRow("文字透明度", self.alpha_spin)

        self.bold_check = QCheckBox("加粗")
        form.addRow("", self.bold_check)

        self.outline_spin = QSpinBox()
        self.outline_spin.setRange(0, 10)
        self.shadow_spin = QSpinBox()
        self.shadow_spin.setRange(0, 10)
        form.addRow("描边 / 阴影", self._with_button(self.outline_spin, self.shadow_spin))

        self.roll_spin = QDoubleSpinBox()
        self.roll_spin.setRange(1.0, 60.0)
        self.roll_spin.setSingleStep(0.5)
        self.roll_spin.setSuffix(" 秒")
        form.addRow("滚动弹幕时长", self.roll_spin)

        self.fix_spin = QDoubleSpinBox()
        self.fix_spin.setRange(1.0, 60.0)
        self.fix_spin.setSingleStep(0.5)
        self.fix_spin.setSuffix(" 秒")
        form.addRow("顶/底弹幕时长", self.fix_spin)

        self.shift_spin = QDoubleSpinBox()
        self.shift_spin.setRange(-60.0, 60.0)
        self.shift_spin.setSingleStep(0.1)
        self.shift_spin.setSuffix(" 秒")
        form.addRow("时间偏移", self.shift_spin)

        self.roll_range_spin = QDoubleSpinBox()
        self.roll_range_spin.setRange(0.1, 1.0)
        self.roll_range_spin.setSingleStep(0.05)
        self.roll_range_spin.setDecimals(2)
        self.fixed_range_spin = QDoubleSpinBox()
        self.fixed_range_spin.setRange(0.1, 1.0)
        self.fixed_range_spin.setSingleStep(0.05)
        self.fixed_range_spin.setDecimals(2)
        form.addRow("滚动 / 固定 屏高占比",
                    self._with_button(self.roll_range_spin, self.fixed_range_spin))

        self.spacing_spin = QSpinBox()
        self.spacing_spin.setRange(0, 100)
        form.addRow("轨道额外间距", self.spacing_spin)

        self.density_spin = QSpinBox()
        self.density_spin.setRange(0, 5000)
        self.density_spin.setSpecialValueText("不限")
        form.addRow("同屏密度上限", self.density_spin)

        self.overlay_check = QCheckBox("允许弹幕重叠")
        self.overlay_check.setToolTip("抢不到轨道时直接叠在已有弹幕上，而不是丢弃")
        form.addRow("", self.overlay_check)

        self.canvas_combo = QComboBox()
        for label, value in CANVAS_PRESETS:
            self.canvas_combo.addItem(label, value)
        _setup_combo(self.canvas_combo)
        self.canvas_combo.currentIndexChanged.connect(self._on_canvas_changed)
        form.addRow("画布分辨率", self.canvas_combo)

        self.canvas_edit = QLineEdit()
        self.canvas_edit.setPlaceholderText("例如 1920x1080")
        form.addRow("自定义画布", self.canvas_edit)

        self.convert_edit = QLineEdit()
        self.convert_edit.setPlaceholderText("s -> r")
        form.addRow("类型转换规则", self.convert_edit)
        form.addRow("", _hint("r=滚动  t=顶部  b=底部  s=纯字幕；可写“s -> r”把字幕转成滚动弹幕"))

        self.block_edit = QLineEdit()
        self.block_edit.setPlaceholderText("多个关键词用英文逗号分隔")
        form.addRow("屏蔽关键词", self.block_edit)

        self.download_check = QCheckBox("缺少弹幕时联网抓取")
        self.download_check.setToolTip("本地没有 danmaku.xml 时从 comment.bilibili.com 下载")
        form.addRow("", self.download_check)

        outer.addWidget(detail)
        return box

    # -- 附属文件 ------------------------------------------------------
    def _group_sidecar(self) -> QGroupBox:
        box = QGroupBox("附属文件")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignRight)

        self.meta_check = QCheckBox("写入 mp4 元数据")
        self.meta_check.setToolTip("title = 视频标题，artist = UP 主，album = 合集")
        form.addRow("", self.meta_check)
        self.cover_check = QCheckBox("导出封面图片")
        self.cover_check.setToolTip("导出为 <视频名>-poster.jpg")
        form.addRow("", self.cover_check)
        self.embed_cover_check = QCheckBox("封面嵌入 mp4")
        form.addRow("", self.embed_cover_check)
        self.nfo_check = QCheckBox("生成 .nfo 刮削文件")
        self.nfo_check.setToolTip("Emby / Jellyfin / Kodi 可直接识别")
        form.addRow("", self.nfo_check)
        self.xml_check = QCheckBox("导出弹幕 XML 副本")
        self.xml_check.setToolTip("把 danmaku.xml 一起复制到输出目录")
        form.addRow("", self.xml_check)

        self.container_combo = QComboBox()
        self.container_combo.addItem("mp4", "mp4")
        self.container_combo.addItem("mkv", "mkv")
        _setup_combo(self.container_combo)
        form.addRow("容器格式", self.container_combo)
        return box

    # -- hash ----------------------------------------------------------
    def _group_hash(self) -> QGroupBox:
        box = QGroupBox("Hash 文件")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignRight)

        self.hash_combo = QComboBox()
        for label, mode, tip in (
            ("生成并用于去重", HashMode.WRITE, "把 .hash 文件写到输出目录，供下次去重"),
            ("仅去重不生成", HashMode.DEDUP_ONLY, "只在内存里算，不留文件"),
            ("完全关闭", HashMode.OFF, "既不算也不写，可能重复转换"),
        ):
            self.hash_combo.addItem(label, mode)
            self.hash_combo.setItemData(self.hash_combo.count() - 1, tip, Qt.ToolTipRole)
        _setup_combo(self.hash_combo)
        form.addRow("策略", self.hash_combo)
        form.addRow("", _hint("hash 内容 = md5(视频字节 ‖ 音频字节)，同一份源文件永远得到同样的值"))
        return box

    # -- 行为 ----------------------------------------------------------
    def _group_behavior(self) -> QGroupBox:
        box = QGroupBox("行为")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignRight)

        self.skip_unfinished_check = QCheckBox("跳过未缓存完成的稿件")
        form.addRow("", self.skip_unfinished_check)
        self.skip_existing_check = QCheckBox("跳过已转换过的视频")
        self.skip_existing_check.setToolTip("先比 .hash，再比体积（容差 1MB）")
        form.addRow("", self.skip_existing_check)
        self.summarize_check = QCheckBox("失败时汇总到「未合并文件」")
        form.addRow("", self.summarize_check)
        self.cleanup_check = QCheckBox("转换后清理中间产物")
        form.addRow("", self.cleanup_check)
        self.open_after_check = QCheckBox("完成后自动打开输出目录")
        form.addRow("", self.open_after_check)

        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 16)
        form.addRow("并发任务数", self.workers_spin)
        form.addRow("", _hint("并发只加速 I/O 与弹幕解析；ffmpeg 本身已多线程。"
                             "中间产物指临时工作目录，永远不碰源文件。"))
        return box

    # -- 引擎 ----------------------------------------------------------
    def _group_engine(self) -> QGroupBox:
        box = QGroupBox("合并引擎")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignRight)

        self.engine_combo = QComboBox()
        for label, mode, tip in (
            ("自动（推荐）", Engine.AUTO, "优先用 MP4Box，找不到则退回 ffmpeg"),
            ("只用 ffmpeg", Engine.FFMPEG, "任何情况都用 ffmpeg 合并"),
            ("只用 MP4Box", Engine.MP4BOX, "MP4Box 不可用时直接报错"),
        ):
            self.engine_combo.addItem(label, mode)
            self.engine_combo.setItemData(self.engine_combo.count() - 1, tip, Qt.ToolTipRole)
        _setup_combo(self.engine_combo)
        form.addRow("引擎", self.engine_combo)

        self.ffmpeg_edit = QLineEdit()
        self.ffmpeg_edit.setPlaceholderText("留空 = 自动查找")
        form.addRow("ffmpeg 路径", self._with_browse(self.ffmpeg_edit, self._pick_ffmpeg,
                                                  is_file=True))

        self.mp4box_edit = QLineEdit()
        self.mp4box_edit.setPlaceholderText("留空 = 自动查找")
        form.addRow("MP4Box 路径", self._with_browse(self.mp4box_edit, self._pick_mp4box,
                                                  is_file=True))

        self.workdir_edit = QLineEdit()
        self.workdir_edit.setPlaceholderText("留空 = 系统临时目录")
        form.addRow("中间产物目录", self._with_browse(self.workdir_edit, self._pick_workdir))
        return box

    # ------------------------------------------------------------------
    # 小工具
    # ------------------------------------------------------------------

    def _with_browse(self, edit: QLineEdit, slot, is_file: bool = False) -> QWidget:
        holder = QWidget()
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(edit, 1)
        btn = QPushButton("…")
        btn.setFixedWidth(28)
        btn.clicked.connect(lambda: slot(edit, is_file))
        lay.addWidget(btn)
        return holder

    def _with_button(self, left: QWidget, right: QWidget) -> QWidget:
        holder = QWidget()
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(left, 1)
        lay.addWidget(right, 1)
        return holder

    def _pick_dir(self, edit: QLineEdit, _is_file: bool = False) -> None:
        start = edit.text().strip() or ""
        chosen = QFileDialog.getExistingDirectory(self, "选择目录", start)
        if chosen:
            edit.setText(chosen)

    def _pick_file(self, edit: QLineEdit, _is_file: bool = True) -> None:
        start = edit.text().strip() or ""
        chosen, _ = QFileDialog.getOpenFileName(self, "选择可执行文件", start,
                                               "可执行文件 (*.exe);;所有文件 (*)")
        if chosen:
            edit.setText(chosen)

    def _pick_import(self, edit: QLineEdit, is_file: bool = False) -> None:
        self._pick_dir(edit, is_file)
        if not self.output_edit.text().strip():
            self.changed.emit()

    def _pick_output(self, edit: QLineEdit, is_file: bool = False) -> None:
        self._pick_dir(edit, is_file)

    def _pick_ffmpeg(self, edit: QLineEdit, is_file: bool = True) -> None:
        self._pick_file(edit, is_file)

    def _pick_mp4box(self, edit: QLineEdit, is_file: bool = True) -> None:
        self._pick_file(edit, is_file)

    def _pick_workdir(self, edit: QLineEdit, is_file: bool = False) -> None:
        self._pick_dir(edit, is_file)

    def _show_template_help(self) -> None:
        lines = [f"{{{key}}} —— {desc}" for key, desc in TEMPLATE_FIELDS.items()]
        QMessageBox.information(
            self, "命名模板占位符",
            "在模板里可以混用这些占位符：\n\n" + "\n".join(lines) +
            "\n\n用 / 分隔可以建子目录，例如：{up}/{title}.{ext}\n"
            "序号可指定宽度：{index:03d} -> 001",
        )

    # ------------------------------------------------------------------
    # 联动
    # ------------------------------------------------------------------

    def _on_layout_changed(self) -> None:
        mode = self.layout_combo.currentData()
        if mode is LayoutMode.TEMPLATE:
            self.template_edit.setEnabled(True)
            if not self.template_edit.text().strip():
                self.template_edit.setText("{title}.mp4")
        else:
            self.template_edit.setEnabled(False)
            self.template_edit.setText(layout_template(mode, ""))
        self._refresh_template_hint()

    def _refresh_template_hint(self) -> None:
        mode = self.layout_combo.currentData()
        if mode is LayoutMode.TEMPLATE:
            self.template_hint.setText("自定义模板，支持建子目录")
        else:
            self.template_hint.setText(f"当前实际模板：{layout_template(mode, '')}")

    def _on_ass_toggled(self, enabled: bool) -> None:
        self.ass_detail.setEnabled(enabled)

    def _on_canvas_changed(self) -> None:
        self.canvas_edit.setEnabled(self.canvas_combo.currentData() == "custom")

    def _canvas_value(self) -> str:
        value = self.canvas_combo.currentData()
        if value == "custom":
            return self.canvas_edit.text().strip() or "auto"
        return value

    # ------------------------------------------------------------------
    # 载入 / 收集
    # ------------------------------------------------------------------

    def load_from(self, opt: Options) -> None:
        """把 ``Options`` 灌进控件。"""
        self.import_edit.setText(opt.import_dir)
        self.output_edit.setText(opt.output_dir)
        self.per_import_check.setChecked(opt.output_per_import)

        idx = self.layout_combo.findData(opt.layout)
        self.layout_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.template_edit.setText(opt.template or "{title}.mp4")
        self._on_layout_changed()

        idx = self.filter_combo.findData(opt.name_filter)
        self.filter_combo.setCurrentIndex(idx if idx >= 0 else 0)
        idx = self.conflict_combo.findData(opt.conflict)
        self.conflict_combo.setCurrentIndex(idx if idx >= 0 else 0)

        ass = opt.ass or AssOptions()
        self.ass_check.setChecked(ass.enabled)
        self.ass_reuse_check.setChecked(ass.reuse_existing)
        self.font_combo.setCurrentText(ass.font_name or "黑体")
        self.font_size_spin.setValue(int(ass.font_size))
        self.alpha_spin.setValue(float(ass.alpha))
        self.bold_check.setChecked(bool(ass.bold))
        self.outline_spin.setValue(int(ass.outline))
        self.shadow_spin.setValue(int(ass.shadow))
        self.roll_spin.setValue(float(ass.roll_time))
        self.fix_spin.setValue(float(ass.fix_time))
        self.shift_spin.setValue(float(ass.time_shift))
        self.roll_range_spin.setValue(float(ass.roll_range))
        self.fixed_range_spin.setValue(float(ass.fixed_range))
        self.spacing_spin.setValue(int(ass.spacing))
        self.density_spin.setValue(int(ass.density))
        self.overlay_check.setChecked(bool(ass.overlay))

        canvas = (ass.canvas or "auto").strip()
        preset = {"auto": 0, "1920x1080": 1, "1280x720": 2}.get(canvas)
        if preset is None:
            self.canvas_combo.setCurrentIndex(3)
            self.canvas_edit.setText(canvas)
        else:
            self.canvas_combo.setCurrentIndex(preset)
            self.canvas_edit.clear()
        self._on_canvas_changed()

        self.convert_edit.setText(ass.convert or "s -> r")
        self.block_edit.setText(",".join(ass.block_keywords or []))
        self.download_check.setChecked(bool(ass.download_missing))
        self._on_ass_toggled(self.ass_check.isChecked())

        self.meta_check.setChecked(opt.write_metadata)
        self.cover_check.setChecked(opt.export_cover)
        self.embed_cover_check.setChecked(opt.embed_cover)
        self.nfo_check.setChecked(opt.write_nfo)
        self.xml_check.setChecked(opt.copy_danmaku_xml)
        idx = self.container_combo.findData((opt.container or "mp4").lower())
        self.container_combo.setCurrentIndex(idx if idx >= 0 else 0)

        idx = self.hash_combo.findData(opt.hash_mode)
        self.hash_combo.setCurrentIndex(idx if idx >= 0 else 0)

        self.skip_unfinished_check.setChecked(opt.skip_unfinished)
        self.skip_existing_check.setChecked(opt.skip_existing)
        self.summarize_check.setChecked(opt.summarize_unmerged)
        self.cleanup_check.setChecked(opt.cleanup_intermediate)
        self.open_after_check.setChecked(opt.open_output_dir)
        self.workers_spin.setValue(max(1, int(opt.workers)))

        idx = self.engine_combo.findData(opt.engine)
        self.engine_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.ffmpeg_edit.setText(opt.ffmpeg_path)
        self.mp4box_edit.setText(opt.mp4box_path)
        self.workdir_edit.setText(opt.work_dir)

    def collect(self, base: Options | None = None) -> Options:
        """从控件读出一份 ``Options``（保留 ``base`` 的未暴露字段）。"""
        opt = base or Options()
        opt.import_dir = self.import_edit.text().strip()
        opt.output_dir = self.output_edit.text().strip()
        opt.output_per_import = self.per_import_check.isChecked()

        opt.layout = self.layout_combo.currentData()
        opt.template = self.template_edit.text().strip() or "{title}.mp4"
        opt.name_filter = self.filter_combo.currentData()
        opt.conflict = self.conflict_combo.currentData()

        ass = opt.ass or AssOptions()
        ass.enabled = self.ass_check.isChecked()
        ass.reuse_existing = self.ass_reuse_check.isChecked()
        ass.font_name = self.font_combo.currentText().strip() or "黑体"
        ass.font_size = self.font_size_spin.value()
        ass.alpha = self.alpha_spin.value()
        ass.bold = self.bold_check.isChecked()
        ass.outline = self.outline_spin.value()
        ass.shadow = self.shadow_spin.value()
        ass.roll_time = self.roll_spin.value()
        ass.fix_time = self.fix_spin.value()
        ass.time_shift = self.shift_spin.value()
        ass.roll_range = self.roll_range_spin.value()
        ass.fixed_range = self.fixed_range_spin.value()
        ass.spacing = self.spacing_spin.value()
        ass.density = self.density_spin.value()
        ass.overlay = self.overlay_check.isChecked()
        ass.canvas = self._canvas_value()
        ass.convert = self.convert_edit.text().strip() or "s -> r"
        ass.block_keywords = [w.strip() for w in self.block_edit.text().split(",") if w.strip()]
        ass.download_missing = self.download_check.isChecked()
        opt.ass = ass

        opt.write_metadata = self.meta_check.isChecked()
        opt.export_cover = self.cover_check.isChecked()
        opt.embed_cover = self.embed_cover_check.isChecked()
        opt.write_nfo = self.nfo_check.isChecked()
        opt.copy_danmaku_xml = self.xml_check.isChecked()
        opt.container = self.container_combo.currentData()

        opt.hash_mode = self.hash_combo.currentData()

        opt.skip_unfinished = self.skip_unfinished_check.isChecked()
        opt.skip_existing = self.skip_existing_check.isChecked()
        opt.summarize_unmerged = self.summarize_check.isChecked()
        opt.cleanup_intermediate = self.cleanup_check.isChecked()
        opt.open_output_dir = self.open_after_check.isChecked()
        opt.workers = self.workers_spin.value()

        opt.engine = self.engine_combo.currentData()
        opt.ffmpeg_path = self.ffmpeg_edit.text().strip()
        opt.mp4box_path = self.mp4box_edit.text().strip()
        opt.work_dir = self.workdir_edit.text().strip()
        return opt


__all__ = ["OptionsPanel", "COMMON_FONTS"]
