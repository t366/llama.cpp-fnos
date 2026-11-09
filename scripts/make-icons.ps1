# 生成 llamacpp 应用图标（原创简笔羊驼，.NET System.Drawing，无第三方依赖）
# 产出：
#   fpk\ICON.PNG (64x64)      fpk\ICON_256.PNG (256x256)
#   fpk\app\ui\images\icon_64.png  fpk\app\ui\images\icon_256.png
Add-Type -AssemblyName System.Drawing

$root = Split-Path -Parent $PSScriptRoot

function New-RoundedPath([float]$x, [float]$y, [float]$w, [float]$h, [float]$r) {
    $p = New-Object System.Drawing.Drawing2D.GraphicsPath
    $d = 2 * $r
    $p.AddArc($x, $y, $d, $d, 180, 90)
    $p.AddArc($x + $w - $d, $y, $d, $d, 270, 90)
    $p.AddArc($x + $w - $d, $y + $h - $d, $d, $d, 0, 90)
    $p.AddArc($x, $y + $h - $d, $d, $d, 90, 90)
    $p.CloseFigure()
    return $p
}

function Draw-Icon([int]$size) {
    $bmp = New-Object System.Drawing.Bitmap($size, $size)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $s = $size / 256.0  # 相对 256 设计稿缩放

    # 背景：深青圆角方块
    $bg = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 20, 78, 96))
    $path = New-RoundedPath (10 * $s) (10 * $s) (236 * $s) (236 * $s) (56 * $s)
    $g.FillPath($bg, $path)

    function S([float]$v) { return $v * $s }

    # 耳朵（细长椭圆，左右各一）
    $earBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 222, 206, 178))
    $g.TranslateTransform((S 88), (S 52))
    $g.RotateTransform(-28)
    $g.FillEllipse($earBrush, (New-Object System.Drawing.RectangleF(0, 0, (S 26), (S 72))))
    $g.ResetTransform()
    $g.TranslateTransform((S 168), (S 52))
    $g.RotateTransform(28)
    $g.FillEllipse($earBrush, (New-Object System.Drawing.RectangleF((S -26), 0, (S 26), (S 72))))
    $g.ResetTransform()

    # 脸（竖椭圆，米白）
    $faceBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 242, 234, 217))
    $g.FillEllipse($faceBrush, (S 83), (S 62), (S 90), (S 128))

    # 刘海（头顶小撮毛，深一点的暖色）
    $hairBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 214, 196, 160))
    $g.FillEllipse($hairBrush, (S 108), (S 54), (S 40), (S 34))

    # 眼睛
    $eyeBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 30, 34, 40))
    $g.FillEllipse($eyeBrush, (S 103), (S 118), (S 12), (S 14))
    $g.FillEllipse($eyeBrush, (S 141), (S 118), (S 12), (S 14))

    # 鼻嘴（小弧线）
    $penColor = [System.Drawing.Color]::FromArgb(255, 90, 96, 104)
    $pen = New-Object System.Drawing.Pen($penColor, (3.4 * $s))
    $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $g.DrawArc($pen, (New-Object System.Drawing.RectangleF((S 116), (S 148), (S 24), (S 16))), 20, 140)

    # 右下角 AI 点缀：两枚电路绿点 + 连线
    $accBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 64, 214, 158))
    $accPen = New-Object System.Drawing.Pen($accBrush.Color, (3.4 * $s))
    $g.FillEllipse($accBrush, (S 196), (S 176), (S 16), (S 16))
    $g.FillEllipse($accBrush, (S 170), (S 208), (S 10), (S 10))
    $g.DrawLine($accPen, (New-Object System.Drawing.PointF((S 204), (S 184))), (New-Object System.Drawing.PointF((S 176), (S 210))))

    $g.Dispose()
    return $bmp
}

$targets = @(
    @{ File = "fpk\ICON.PNG";                Size = 64 },
    @{ File = "fpk\ICON_256.PNG";            Size = 256 },
    @{ File = "fpk\app\ui\images\icon_64.png";  Size = 64 },
    @{ File = "fpk\app\ui\images\icon_256.png"; Size = 256 }
)
foreach ($t in $targets) {
    $dir = Split-Path -Parent (Join-Path $root $t.File)
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $bmp = Draw-Icon $t.Size
    $out = Join-Path $root $t.File
    $bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    Write-Output "generated: $($t.File) ($($t.Size)x$($t.Size))"
}
