import React, { useEffect, useState } from 'react';
import { FileText } from 'lucide-react';
import { api } from './api';
import type { Paper } from './types';

interface StandalonePdfReaderProps {
  paperId: string;
  initialPage: number;
}

export const StandalonePdfReader: React.FC<StandalonePdfReaderProps> = ({
  paperId,
  initialPage,
}) => {
  const [paper, setPaper] = useState<Paper | null>(null);
  const [pdfUrl, setPdfUrl] = useState('');
  const [draftPage, setDraftPage] = useState(String(initialPage));
  const [currentPage, setCurrentPage] = useState(initialPage);
  const [loadError, setLoadError] = useState('');

  useEffect(() => {
    let isCurrent = true;
    
    // Fetch paper metadata
    api.getPaper(paperId)
      .then(p => {
        if (isCurrent) setPaper(p);
      })
      .catch(e => {
        if (isCurrent) setLoadError(e.message || '无法获取论文信息');
      });

    return () => { isCurrent = false; };
  }, [paperId]);

  useEffect(() => {
    let isCurrent = true;
    setLoadError('');
    setDraftPage(String(currentPage));
    api.getPaperPdfUrl(paperId, currentPage)
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
  }, [paperId, currentPage]);

  const commitPageChange = () => {
    const parsed = Number.parseInt(draftPage, 10);
    if (!Number.isFinite(parsed) || parsed <= 0) {
      setDraftPage(String(currentPage));
      return;
    }
    setCurrentPage(parsed);
  };

  return (
    <div className="pdf-reader-shell" style={{ border: 'none', borderRadius: 0, height: '100vh' }}>
      <div className="pdf-reader-toolbar">
        <div className="pdf-reader-title" style={{ paddingLeft: '16px' }}>
          <FileText size={17} />
          <div>
            <strong>{paper?.title || '加载中...'}</strong>
            <span>独立阅读窗口</span>
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
              if (event.key === 'Escape') setDraftPage(String(currentPage));
            }}
          />
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
            title={`${paper?.title || '论文'} PDF 原文`}
            style={{ borderRadius: 0 }}
          />
        ) : (
          <div className="pdf-reader-empty">
            <div className="spinner" aria-hidden="true" />
            <h3>正在打开 PDF</h3>
            <p>准备跳转到第 {currentPage} 页。</p>
          </div>
        )}
      </div>
    </div>
  );
};
