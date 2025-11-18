"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Maximize, Minimize, Loader2 } from "lucide-react";

type ConnectionState = "idle" | "connecting" | "connected" | "error";

async function postJSON(url: string, body: unknown) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`Request failed (${response.status})`);
  }
  return response.json();
}

async function getJSON(url: string) {
  const response = await fetch(url, {
    method: "GET",
    headers: { "Content-Type": "application/json" },
  });
  if (!response.ok) {
    throw new Error(`Request failed (${response.status})`);
  }
  return response.json();
}

export function BrowserScreenStream() {
  const [state, setState] = useState<ConnectionState>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [isVideoPlaying, setIsVideoPlaying] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const pendingCandidatesRef = useRef<RTCIceCandidateInit[]>([]);
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hasAutoConnectedRef = useRef(false);
  const videoPlayingHandlerRef = useRef<(() => void) | null>(null);

  const reset = useCallback(() => {
    setState("idle");
    setErrorMessage(null);
    setIsVideoPlaying(false);
    sessionIdRef.current = null;
  }, []);

  const flushCandidates = useCallback(async () => {
    const candidates = pendingCandidatesRef.current.splice(0);
    if (!candidates.length || !sessionIdRef.current) {
      return;
    }
    await postJSON("/api/screen-stream/ice", {
      session_id: sessionIdRef.current,
      candidate: candidates[0],
    });
  }, []);

  const stopConnection = useCallback(() => {
    if (flushTimerRef.current) {
      clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }

    const pc = pcRef.current;
    pcRef.current = null;

    if (pc) {
      pc.close();
    }

    const video = videoRef.current;
    if (video) {
      // Remove event listener
      if (videoPlayingHandlerRef.current) {
        video.removeEventListener("playing", videoPlayingHandlerRef.current);
        videoPlayingHandlerRef.current = null;
      }
      
      if (video.srcObject instanceof MediaStream) {
        video.srcObject.getTracks().forEach((track) => track.stop());
        video.srcObject = null;
      }
    }

    reset();
  }, [reset]);

  const toggleFullscreen = useCallback(async () => {
    if (!containerRef.current) return;

    try {
      if (!document.fullscreenElement) {
        await containerRef.current.requestFullscreen();
        setIsFullscreen(true);
      } else {
        await document.exitFullscreen();
        setIsFullscreen(false);
      }
    } catch (error) {
      console.error("Fullscreen error:", error);
    }
  }, []);

  const startConnection = useCallback(async () => {
    try {
      stopConnection();
      setState("connecting");

      // Fetch ICE servers from backend
      const iceConfig = await getJSON("/api/screen-stream/ice-servers");
      
      const pc = new RTCPeerConnection(iceConfig);
      pcRef.current = pc;

      const remoteStream = new MediaStream();
      if (videoRef.current) {
        videoRef.current.srcObject = remoteStream;
        videoRef.current.autoplay = true;
        videoRef.current.playsInline = true;
        
        // Listen for when video starts playing
        const handlePlaying = () => {
          setIsVideoPlaying(true);
        };
        
        videoPlayingHandlerRef.current = handlePlaying;
        videoRef.current.addEventListener("playing", handlePlaying);
      }

      pc.ontrack = (event) => {
        event.streams[0]?.getTracks().forEach((track) => {
          remoteStream.addTrack(track);
        });
      };

      pc.onicecandidate = (event) => {
        if (!event.candidate) {
          return;
        }
        pendingCandidatesRef.current.push(event.candidate.toJSON());
        if (flushTimerRef.current) {
          clearTimeout(flushTimerRef.current);
        }
        flushTimerRef.current = setTimeout(() => {
          flushTimerRef.current = null;
          flushCandidates().catch((error) => {
            console.error("Failed to flush ICE candidates", error);
          });
        }, 300);
      };

      pc.onconnectionstatechange = () => {
        if (pc.connectionState === "connected") {
          setState("connected");
        } else if (pc.connectionState === "failed") {
          setErrorMessage("Connection failed");
          setState("error");
        }
      };

      // Create offer
      const offer = await pc.createOffer({
        offerToReceiveAudio: false,
        offerToReceiveVideo: true,
      });
      await pc.setLocalDescription(offer);

      // Set high bitrate for better quality (50 Mbps)
      const sender = pc.getSenders().find(s => s.track?.kind === 'video');
      if (sender) {
        const parameters = sender.getParameters();
        if (!parameters.encodings) {
          parameters.encodings = [{}];
        }
        parameters.encodings[0].maxBitrate = 50000000; // 50 Mbps
        await sender.setParameters(parameters);
        console.log('Set video bitrate to 50 Mbps for high quality');
      }

      // Send offer to backend
      const answer = await postJSON("/api/screen-stream/offer", {
        sdp: offer.sdp,
        type: offer.type,
      });
      
      sessionIdRef.current = answer.session_id;
      
      // Set remote description (answer)
      await pc.setRemoteDescription(new RTCSessionDescription({
        sdp: answer.sdp,
        type: answer.type,
      }));
    } catch (error) {
      console.error("Failed to start screen stream", error);
      setErrorMessage(error instanceof Error ? error.message : "Unknown error");
      setState("error");
    }
  }, [flushCandidates, stopConnection]);

  // Auto-connect when component mounts
  useEffect(() => {
    if (!hasAutoConnectedRef.current) {
      hasAutoConnectedRef.current = true;
      startConnection();
    }

    return () => {
      stopConnection();
    };
  }, [startConnection, stopConnection]);

  // Listen for fullscreen changes
  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(!!document.fullscreenElement);
    };

    document.addEventListener("fullscreenchange", handleFullscreenChange);
    return () => {
      document.removeEventListener("fullscreenchange", handleFullscreenChange);
    };
  }, []);

  return (
    <div className="w-full">
      <div 
        ref={containerRef}
        className="relative aspect-video w-full overflow-hidden rounded-3xl border bg-black shadow-2xl"
      >
        {(state === "idle" || state === "connecting" || (state === "connected" && !isVideoPlaying)) && (
          <div className="absolute inset-0 flex flex-col items-center justify-center bg-black/80 z-10 gap-3">
            <Loader2 className="h-8 w-8 text-white animate-spin" />
            <div className="text-white text-sm">
              {state === "idle" || state === "connecting" 
                ? "Connecting to browser stream..." 
                : "Loading video stream..."}
            </div>
          </div>
        )}
        {state === "error" && errorMessage && (
          <div className="absolute inset-0 flex flex-col items-center justify-center bg-black/80 z-10 gap-2">
            <div className="text-red-500 text-sm">{errorMessage}</div>
            <button
              onClick={startConnection}
              className="text-xs text-white underline hover:text-gray-300"
            >
              Retry
            </button>
          </div>
        )}
        
        {/* Fullscreen button - only visible on mobile */}
        {state === "connected" && (
          <button
            onClick={toggleFullscreen}
            className="lg:hidden absolute top-2 right-2 z-20 bg-black/60 hover:bg-black/80 text-white p-2 rounded-lg transition-all"
            aria-label={isFullscreen ? "Exit fullscreen" : "Enter fullscreen"}
          >
            {isFullscreen ? (
              <Minimize className="h-5 w-5" />
            ) : (
              <Maximize className="h-5 w-5" />
            )}
          </button>
        )}
        
        <video
          ref={videoRef}
          className="h-full w-full object-contain"
          playsInline
          autoPlay
        />
      </div>
    </div>
  );
}

