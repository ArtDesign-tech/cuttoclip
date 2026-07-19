import { useCallback, useEffect, useRef, useState } from "react";
import type { HighlightCandidate, LayoutPreviewPlan } from "../types";
import { SignalMark } from "./common";

type Props = {
  clip: HighlightCandidate;
  sourceUrl: string;
  showVideo: boolean;
  plan: LayoutPreviewPlan | null;
  videoRef: React.RefObject<HTMLVideoElement | null>;
  onTimeUpdate: () => void;
  onPlay: () => void;
  onPause: () => void;
  onLoadedMetadata: () => void;
};

function drawCover(context: CanvasRenderingContext2D, video: HTMLVideoElement, width: number, height: number) {
  const sourceRatio = video.videoWidth / video.videoHeight;
  const targetRatio = width / height;
  let sx = 0; let sy = 0; let sw = video.videoWidth; let sh = video.videoHeight;
  if (sourceRatio > targetRatio) {
    sw = video.videoHeight * targetRatio;
    sx = (video.videoWidth - sw) / 2;
  } else {
    sh = video.videoWidth / targetRatio;
    sy = (video.videoHeight - sh) / 2;
  }
  context.drawImage(video, sx, sy, sw, sh, 0, 0, width, height);
}

function drawContain(context: CanvasRenderingContext2D, video: HTMLVideoElement, width: number, height: number) {
  const scale = Math.min(width / video.videoWidth, height / video.videoHeight);
  const renderedWidth = video.videoWidth * scale;
  const renderedHeight = video.videoHeight * scale;
  context.drawImage(video, (width - renderedWidth) / 2, (height - renderedHeight) / 2, renderedWidth, renderedHeight);
}

export function OutputPreviewCanvas({ clip, sourceUrl, showVideo, plan, videoRef, onTimeUpdate, onPlay, onPause, onLoadedMetadata }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const frameRef = useRef(0);
  const [canvasFailed, setCanvasFailed] = useState(false);
  const portrait = clip.presentation.layout !== "landscape";
  const canvasWidth = portrait ? 540 : 960;
  const canvasHeight = portrait ? 960 : 540;

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const video = videoRef.current;
    if (!canvas || !video || video.readyState < 2 || !video.videoWidth || !video.videoHeight) return;
    const context = canvas.getContext("2d");
    if (!context) return;
    try {
      context.fillStyle = "#05070a";
      context.fillRect(0, 0, canvas.width, canvas.height);
      if (plan?.keyframes.length) {
        const relativeTime = Math.max(0, video.currentTime - clip.startSeconds);
        let keyframe = plan.keyframes[0];
        for (const candidate of plan.keyframes) {
          if (candidate.atSeconds > relativeTime) break;
          keyframe = candidate;
        }
        for (const layer of keyframe.layers) {
          const source = layer.source;
          const destination = layer.destination;
          context.drawImage(
            video,
            source.x * video.videoWidth,
            source.y * video.videoHeight,
            source.width * video.videoWidth,
            source.height * video.videoHeight,
            destination.x * canvas.width,
            destination.y * canvas.height,
            destination.width * canvas.width,
            destination.height * canvas.height,
          );
        }
      } else if (portrait) drawCover(context, video, canvas.width, canvas.height);
      else drawContain(context, video, canvas.width, canvas.height);
      setCanvasFailed(false);
    } catch {
      setCanvasFailed(true);
    }
  }, [clip.startSeconds, plan, portrait, videoRef]);

  const startDrawing = useCallback(() => {
    window.cancelAnimationFrame(frameRef.current);
    const tick = () => {
      draw();
      if (!videoRef.current?.paused) frameRef.current = window.requestAnimationFrame(tick);
    };
    tick();
  }, [draw, videoRef]);

  useEffect(() => {
    startDrawing();
    return () => window.cancelAnimationFrame(frameRef.current);
  }, [startDrawing]);

  if (!showVideo) return <div className="demo-video"><SignalMark /></div>;

  return <>
    <video
      ref={videoRef}
      className={canvasFailed ? "preview-source is-fallback" : "preview-source"}
      src={sourceUrl}
      crossOrigin="anonymous"
      preload="metadata"
      playsInline
      onLoadedMetadata={() => { onLoadedMetadata(); draw(); }}
      onSeeked={draw}
      onTimeUpdate={() => { onTimeUpdate(); draw(); }}
      onPlay={() => { onPlay(); startDrawing(); }}
      onPause={() => { onPause(); draw(); }}
    />
    {!canvasFailed && <canvas ref={canvasRef} width={canvasWidth} height={canvasHeight} aria-label="Output frame preview" />}
  </>;
}
