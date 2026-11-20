import { StrictMode, Suspense, lazy } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import Layout from './components/Layout'
import { Spinner, ToastProvider } from './components/ui'

// Route-level code splitting: each menu page is its own chunk
const Dashboard = lazy(() => import('./pages/Dashboard'))
const Ingestion = lazy(() => import('./pages/Ingestion'))
const UploadFlow = lazy(() => import('./pages/upload/UploadFlow'))
const UploadStep = lazy(() => import('./pages/upload/UploadSteps').then((m) => ({ default: m.UploadStep })))
const SectionStep = lazy(() => import('./pages/upload/UploadSteps').then((m) => ({ default: m.SectionStep })))
const DocumentsStep = lazy(() => import('./pages/upload/UploadSteps').then((m) => ({ default: m.DocumentsStep })))
const ReviewStep = lazy(() => import('./pages/upload/UploadSteps').then((m) => ({ default: m.ReviewStep })))
const Documents = lazy(() => import('./pages/Documents'))
const Pipeline = lazy(() => import('./pages/Pipeline'))
const Security = lazy(() => import('./pages/Security'))
const Events = lazy(() => import('./pages/Events'))
const AiSecurity = lazy(() => import('./pages/AiSecurity'))
const DataQuality = lazy(() => import('./pages/DataQuality'))
const Requirements = lazy(() => import('./pages/Requirements'))

const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 5_000, refetchOnWindowFocus: false } } })

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <BrowserRouter>
          <Suspense fallback={<div className="empty"><Spinner /></div>}>
            <Routes>
              <Route element={<Layout />}>
                <Route index element={<Dashboard />} />
                <Route path="upload" element={<UploadFlow />}>
                  <Route index element={<UploadStep />} />
                  <Route path="request" element={<SectionStep section="request" />} />
                  <Route path="member" element={<SectionStep section="member" />} />
                  <Route path="documents" element={<DocumentsStep />} />
                  <Route path="review" element={<ReviewStep />} />
                </Route>
                <Route path="ingestion" element={<Ingestion />} />
                <Route path="documents" element={<Documents />} />
                <Route path="pipeline" element={<Pipeline />} />
                <Route path="security" element={<Security />} />
                <Route path="events" element={<Events />} />
                <Route path="ai-security" element={<AiSecurity />} />
                <Route path="data-quality" element={<DataQuality />} />
                <Route path="requirements" element={<Requirements />} />
                <Route path="*" element={<div className="empty">Page not found</div>} />
              </Route>
            </Routes>
          </Suspense>
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
)
