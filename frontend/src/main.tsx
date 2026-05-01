import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { StandalonePdfReader } from './StandalonePdfReader';
import './styles.css';

const params = new URLSearchParams(window.location.search);
const pdfPaperId = params.get('pdfPaperId');
const pdfPage = params.get('pdfPage') ? parseInt(params.get('pdfPage') as string, 10) : 1;

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    {pdfPaperId ? <StandalonePdfReader paperId={pdfPaperId} initialPage={pdfPage} /> : <App />}
  </React.StrictMode>,
);

