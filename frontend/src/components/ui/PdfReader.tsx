import React, { useEffect, useState } from 'react';
import { ArrowLeft, ExternalLink, FileText } from 'lucide-react';
import { api } from '../../api';
import type { PdfReaderTarget } from '../../types';

interface PdfReaderProps {
  target: PdfReaderTarget;
  onClose: () => void;
  onPageChange: (pageNumber: number) => void;
}

export const PdfReader: React.FC<PdfReaderProps> = ({
  target,
  onClose,
  onPageChange,
}) => {
  const [pdfUrl, setPdfUrl] = useState('');
  const [draftPage, setDraftPage] = useState(String(target.pageNumber));
  const [loadError, setLoadError] = useState('');

  useEffect(() => {
    let isCurrent = true;
    setLoadError('');
    setDraftPage(String(target.pageNumber));
    api.getPaperPdfUrl(target.paper.id, target.pageNumber)
      .then((url) => {
        if (isCurrent) setPdfUrl(url);
      })
      .catch((error) => {
        if (isCurrent) {
          setPdfUrl('');
          setLoadError(error instanceof Error ? error.message : 'PDF 地址生成失败。');
        }
      });

    return () => {
      isCurrent = false;
    };
  }, [target.paper.id, target.pageNumber]);

  const commitPageChange = () => {
    const parsed = Number.parseInt(draftPage, 10);
    if (!Number.isFinite(parsed) || parsed <= 0) {
      setDraftPage(String(target.pageNumber));
      return;
    }
    onPageChange(parsed);
  };

  return (
    <div className="pdf-reader-shell">
      <div className="pdf-reader-toolbar">
        <button
          type="button"
          className="btn-reader-back"
          onClick={onClose}
          aria-label="返回资源库"
        >
          <ArrowLeft size={16} />
          <span>返回</span>
        </button>

        <div className="pdf-reader-title">
          <FileText size={17} />
          <div>
            <strong>{target.paper.title || '未命名论文'}</strong>
            <span>{target.sourceLabel || 'PDF 原文'}</span>
          </div>
        </div>

        <div className="pdf-reader-controls">
          <label htmlFor="pdf-reader-page">页码</label>
          <input
            id="pdf-reader-page"
            type="number"
            min={1}
            value={draftPage}
            onChange={(event) => setDraftPage(event.target.value)}
            onBlur={commitPageChange}
            onKeyDown={(event) => {
              if (event.key === 'Enter') commitPageChange();
              if (event.key === 'Escape') setDraftPage(String(target.pageNumber));
            }}
          />
          {pdfUrl && (
            <a
              className="btn-open-pdf-new"
              href={pdfUrl}
              target="_blank"
              rel="noreferrer"
              aria-label="在新窗口打开 PDF"
              title="在新窗口打开 PDF"
            >
              <ExternalLink size={15} />
            </a>
          )}
        </div>
      </div>

      <div className="pdf-reader-stage">
        {loadError ? (
          <div className="pdf-reader-empty">
            <FileText size={36} />
            <h3>无法打开 PDF</h3>
            <p>{loadError}</p>
          </div>
        ) : pdfUrl ? (
          <iframe
            key={pdfUrl}
            className="pdf-reader-frame"
            src={pdfUrl}
            title={`${target.paper.title || '论文'} PDF 原文`}
          />
        ) : (
          <div className="pdf-reader-empty">
            <div className="spinner" aria-hidden="true" />
            <h3>正在打开 PDF</h3>
            <p>准备跳转到第 {target.pageNumber} 页。</p>
          </div>
        )}
      </div>
    </div>
  );
};
