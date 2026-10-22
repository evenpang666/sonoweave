# README 视觉素材

| 素材 | 用途 | 来源 |
| --- | --- | --- |
| [banner.png](assets/banner.png) | 开头的 Logo 与项目名横幅 | 内置 imagegen 生成 |
| [subtitle-preview.png](assets/subtitle-preview.png) | 示例字幕界面 | 实际 CaptionWindow 离屏渲染 |
| [model-menu.png](assets/model-menu.png) | 4B / 2B 选择菜单 | 实际右键菜单离屏渲染 |

这些是用于发布文档的固定素材，随源码维护；运行诊断、验证截图仍写入被忽略的目录。界面插图不采集声音，不加载模型，也不代表现场翻译效果。

重新生成界面插图：

```powershell
.venv\Scripts\python.exe tools\render_readme_previews.py
```

## 横幅生成提示词

使用内置 imagegen，生成后复制到项目内，不依赖用户目录中的原始输出。以下为最终完整提示词：

```text
Use case: logo-brand.
Asset type: final GitHub README header banner for a Windows offline subtitle translator, not a mockup.
Create a refined minimal horizontal brand banner in a 3:1 aspect ratio, approximately 1536 by 512. Deep ink navy background (#101820) with barely perceptible elegant tonal depth. A single centered horizontal brand lockup: on the left, a distinctive simple mint/turquoise logo combining the idea of flowing sound waves and a small island into one confident rounded continuous symbol; to its right, the project name. Logo about 110px tall and name dominant and clearly legible. Spacious composition, perfectly balanced vertical alignment and generous outer margins. Professional calm open source product identity. Restrained mint accent (#79DBC2), soft white lettering, clean modern typography. Flat precise graphic design, crisp edges, no photographic objects, no dramatic glow, no busy wave decorations, no boxes, no frames.
The ONLY text in the whole image must be exactly: "声屿 SonoWeave"
Render both Chinese characters accurately and the English brand name with exact capitalization S-o-n-o-W-e-a-v-e. One line. Both Chinese and English are the project name, not subtitles.
ABSOLUTELY NO tagline, NO explanatory small text, NO category labels, NO extra words, NO watermark, NO subtitles, NO UI panels. This banner contains ONLY the logo and the project name on the dark background.
```
