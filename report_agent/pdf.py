"""LibreOffice独立用户配置转换；失败时不接受残留旧PDF。"""
import shutil
import subprocess
import tempfile
from pathlib import Path
from .common import digest

def convert(docx, executable=None):
    docx=Path(docx).resolve();target=docx.with_suffix('.pdf')
    exe=executable or shutil.which('libreoffice') or shutil.which('soffice')
    if not exe:raise RuntimeError('未找到LibreOffice；安装libreoffice或在runtime中指定libreoffice路径')
    with tempfile.TemporaryDirectory(prefix='report_pdf_') as folder:
        tmp=Path(folder);profile=(tmp/'profile').as_uri()
        cmd=[str(exe),'-env:UserInstallation='+profile,'--headless','--convert-to','pdf:writer_pdf_Export','--outdir',str(tmp),str(docx)]
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=240)
        candidate=tmp/target.name
        if p.returncode or not candidate.is_file() or candidate.stat().st_size<500:
            raise RuntimeError('PDF转换失败：'+p.stdout[-600:]+p.stderr[-600:])
        import pymupdf as fitz
        with fitz.open(candidate) as pdf:
            if pdf.page_count<1:raise RuntimeError('PDF没有有效页面')
            empty=[i+1 for i,page in enumerate(pdf) if not page.get_text().strip() and not page.get_images()]
            if empty:raise RuntimeError('PDF出现空白页：'+str(empty))
            pages=pdf.page_count
        shutil.copyfile(candidate,target)
    return {'path':str(target),'sha256':digest(target),'source_docx_sha256':digest(docx),'pages':pages,'status':'converted',
            'automatic_checks':'PDF可解析、非零页且无全空页；不代替人工检查分页、字体及表图遮挡','visual_review':'pending'}
