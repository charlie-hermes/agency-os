"use client";

export default function ErrorBoundary({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <main className="error-page"><span className="eyebrow accent">Safe interruption</span><h1>Fleet could not safely show this view.</h1><p>No change was recorded. Sign in again or ask Fleet to check your access.</p><button className="primary-button" onClick={reset}>Try again</button></main>;
}
