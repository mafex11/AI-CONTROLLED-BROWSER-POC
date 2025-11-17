"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Mic, Send, Loader2, Zap, ChevronLeft, ChevronRight, ChevronDown, Square } from "lucide-react";
import { NarrationDisplay } from "@/components/narration-display";
import { ScreenshotDisplay } from "@/components/screenshot-display";
import { useVoiceWebSocket } from "@/components/voice-websocket";
import { Chat, ChatMessage } from "@/components/chat";
import { BrowserScreenStream } from "@/components/browser-screen-stream";

interface StepData {
  step: number;
  narration: string;
  tool?: string;
  screenshot?: string;
  timestamp: Date;
}

type LLMProvider = "gemini" | "claude" | "openai";

const PROVIDER_INFO: Record<LLMProvider, { label: string; speed: string }> = {
  gemini: { label: "Gemini", speed: "~2s per call" },
  claude: { label: "Claude", speed: "~3.75s per call" },
  openai: { label: "OpenAI", speed: "~4s per call" },
};

export default function Home() {
  const [query, setQuery] = useState("");
  const [provider, setProvider] = useState<LLMProvider>("gemini");
  const [isVoiceMode, setIsVoiceMode] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [steps, setSteps] = useState<StepData[]>([]);
  const [voiceStatus, setVoiceStatus] = useState<string>("");
  const [isSubmitted, setIsSubmitted] = useState(false);
  const [currentSlideIndex, setCurrentSlideIndex] = useState(0);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [processedSteps, setProcessedSteps] = useState<Set<string>>(new Set());
  const [isProviderDropdownOpen, setIsProviderDropdownOpen] = useState(false);
  const [hasNavigated, setHasNavigated] = useState(false);
  const providerDropdownRef = useRef<HTMLDivElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const readerRef = useRef<ReadableStreamDefaultReader<Uint8Array> | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const isPlayingAudioRef = useRef(false);
  const activeAudioSourcesRef = useRef<AudioBufferSourceNode[]>([]);
  const audioQueueRef = useRef<Array<{ audioBase64: string; sampleRate: number; numChannels: number }>>([]);
  const isProcessingAudioQueueRef = useRef(false);
  const nextPlayTimeRef = useRef<number | null>(null);
  const sendVoiceMessageRef = useRef<((message: Record<string, unknown>) => void) | null>(null);

  // Initialize AudioContext for voice mode
  useEffect(() => {
    if (isVoiceMode && !audioContextRef.current) {
      const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)();
      audioContextRef.current = audioContext;
    }
    
    return () => {
      if (audioContextRef.current) {
        audioContextRef.current.close().catch(() => {});
        audioContextRef.current = null;
      }
    };
  }, [isVoiceMode]);

  // Stop all currently playing audio
  const stopAllAudio = useCallback(() => {
    activeAudioSourcesRef.current.forEach((source) => {
      try {
        source.stop();
      } catch (e) {
        // Source may already be stopped
      }
    });
    activeAudioSourcesRef.current = [];
    isPlayingAudioRef.current = false;
    // Clear audio queue
    audioQueueRef.current = [];
    isProcessingAudioQueueRef.current = false;
    nextPlayTimeRef.current = null;
  }, []);

  // Process audio queue sequentially with back-to-back playback
  const processAudioQueue = useCallback(async () => {
    if (isProcessingAudioQueueRef.current || !audioContextRef.current) {
      return;
    }

    isProcessingAudioQueueRef.current = true;

    // Resume AudioContext if suspended (required for user interaction)
    if (audioContextRef.current.state === 'suspended') {
      await audioContextRef.current.resume();
    }

    // Get start time - either now or continue from where we left off
    let currentTime = nextPlayTimeRef.current ?? audioContextRef.current.currentTime;
    if (currentTime < audioContextRef.current.currentTime) {
      currentTime = audioContextRef.current.currentTime;
    }

    while (audioQueueRef.current.length > 0) {
      const chunk = audioQueueRef.current.shift();
      if (!chunk) break;

      try {
        // Decode base64 to binary string, then to Uint8Array
        const binaryString = atob(chunk.audioBase64);
        const audioData = new Uint8Array(binaryString.length);
        for (let i = 0; i < binaryString.length; i++) {
          audioData[i] = binaryString.charCodeAt(i);
        }

        // Create AudioBuffer from PCM data
        // PCM16 = 2 bytes per sample, so total samples = bytes / 2
        const samplesPerChannel = audioData.length / (2 * chunk.numChannels);
        const audioBuffer = audioContextRef.current.createBuffer(
          chunk.numChannels,
          samplesPerChannel,
          chunk.sampleRate
        );
        
        // Convert PCM16 bytes to Float32 samples
        const dataView = new DataView(audioData.buffer);
        for (let channel = 0; channel < chunk.numChannels; channel++) {
          const channelData = audioBuffer.getChannelData(channel);
          for (let i = 0; i < samplesPerChannel; i++) {
            // Interleaved PCM: sample format is [L, R, L, R, ...] for stereo
            const byteIndex = (i * chunk.numChannels + channel) * 2;
            const int16 = dataView.getInt16(byteIndex, true); // little-endian
            channelData[i] = int16 / 32768.0; // Convert to [-1, 1] range
          }
        }

        // Calculate duration of this chunk
        const duration = audioBuffer.duration;

        // Create AudioBufferSourceNode and schedule to play back-to-back
        const source = audioContextRef.current.createBufferSource();
        source.buffer = audioBuffer;
        source.connect(audioContextRef.current.destination);
        
        // Track this source so we can stop it on interruption
        activeAudioSourcesRef.current.push(source);
        
        // Schedule playback at currentTime (back-to-back with previous chunk)
        source.start(currentTime);
        
        // Update currentTime for next chunk (play immediately after this one)
        currentTime += duration;
        nextPlayTimeRef.current = currentTime;

        // Clean up when done
        source.onended = () => {
          // Remove from active sources when done
          activeAudioSourcesRef.current = activeAudioSourcesRef.current.filter(s => s !== source);
          if (activeAudioSourcesRef.current.length === 0) {
            isPlayingAudioRef.current = false;
            // Reset next play time when all audio is done
            if (audioQueueRef.current.length === 0) {
              nextPlayTimeRef.current = null;
              // Signal backend that audio playback is complete
              if (sendVoiceMessageRef.current) {
                console.log('Audio playback complete, signaling backend');
                sendVoiceMessageRef.current({ type: 'audio_playback_complete' });
              }
            }
          }
        };

        isPlayingAudioRef.current = true;
      } catch (error) {
        console.error('Error playing audio chunk:', error);
      }
    }

    isProcessingAudioQueueRef.current = false;
    
    // If there are still chunks in queue, process them (use setTimeout to avoid recursion)
    if (audioQueueRef.current.length > 0) {
      setTimeout(() => {
        if (!isProcessingAudioQueueRef.current) {
          processAudioQueue();
        }
      }, 0);
    }
  }, []);

  // Queue audio chunks for sequential playback
  const playAudioChunk = useCallback(async (audioBase64: string, sampleRate: number, numChannels: number) => {
    if (!audioContextRef.current) {
      return;
    }

    // Add to queue
    audioQueueRef.current.push({ audioBase64, sampleRate, numChannels });
    
    // Start processing queue if not already processing
    processAudioQueue();
  }, [processAudioQueue]);

  const { isConnected: isVoiceConnected, isConnecting: isVoiceConnecting, sendMessage: sendVoiceMessage, disconnect: disconnectVoice } = useVoiceWebSocket({
    isEnabled: isVoiceMode,
    onUserSpeech: (text) => {
      if (!text.trim()) return;
      
      // Stop all audio when user starts speaking (interruption)
      stopAllAudio();
      
      setVoiceStatus(`You: ${text}`);
      
      // Set submitted state to show chat
      if (!isSubmitted) {
        setIsSubmitted(true);
      }
      
      // Reset processed steps for new query
      setProcessedSteps(new Set());
      // Don't reset hasNavigated - keep stream alive once started
      
      // Add user message to chat and thinking message
      setChatMessages((prev) => {
        // Check if we already have a thinking message (processing in progress)
        const hasThinking = prev.some(msg => msg.isLoading && msg.type === "agent");
        
        if (!hasThinking) {
          return [
            ...prev,
            {
              id: `user-${Date.now()}`,
              type: "user",
              content: text,
              timestamp: new Date(),
            },
            {
              id: `thinking-${Date.now()}`,
              type: "agent",
              content: "Thinking...",
              timestamp: new Date(),
              isLoading: true,
            },
          ];
        } else {
          // Update the last user message if we're already processing
          let lastUserIndex = -1;
          for (let i = prev.length - 1; i >= 0; i--) {
            if (prev[i].type === "user") {
              lastUserIndex = i;
              break;
            }
          }
          
          if (lastUserIndex >= 0) {
            const updated = [...prev];
            updated[lastUserIndex] = {
              ...updated[lastUserIndex],
              content: text,
              timestamp: new Date(),
            };
            return updated;
          }
          // If no user message found, add one
          return [
            ...prev,
            {
              id: `user-${Date.now()}`,
              type: "user",
              content: text,
              timestamp: new Date(),
            },
          ];
        }
      });
    },
    onAgentResponse: (text) => {
      if (!text.trim()) return;
      
      setVoiceStatus(`Agent: ${text}`);
      
      // Remove thinking message and add agent response if it's a standalone response
      // (Note: step messages are handled separately in onStep)
      setChatMessages((prev) => {
        const filtered = prev.filter((msg) => !(msg.isLoading && msg.type === "agent"));
        
        // Only add if it's not already a step message
        const isStepMessage = filtered.some(
          (msg) => msg.type === "agent" && msg.content === text
        );
        
        if (!isStepMessage) {
          return [
            ...filtered,
            {
              id: `agent-response-${Date.now()}`,
              type: "agent",
              content: text,
              timestamp: new Date(),
            },
          ];
        }
        
        return filtered;
      });
    },
    onStep: (stepData) => {
      // Check if this step involves navigation
      if (stepData.tool && stepData.tool.toLowerCase().includes('navigate')) {
        setHasNavigated(true);
      }
      
      // Add step with narration and screenshot
      setSteps((prev) => {
        // Check if step already exists (update it) or add new one
        const existingIndex = prev.findIndex((s) => s.step === stepData.step);
        const isNewStep = existingIndex < 0;
        
        if (existingIndex >= 0) {
          const updated = [...prev];
          updated[existingIndex] = {
            ...updated[existingIndex],
            narration: stepData.narration || updated[existingIndex].narration,
            screenshot: stepData.screenshot || updated[existingIndex].screenshot,
          };
          return updated;
        }
        const newSteps = [
          ...prev,
          {
            step: stepData.step,
            narration: stepData.narration,
            screenshot: stepData.screenshot,
            timestamp: new Date(),
          },
        ];
        // Auto-advance to the new slide
        setCurrentSlideIndex(newSteps.length - 1);

        return newSteps;
      });

      // Always add agent response to chat if there's narration
      // This ensures all responses from voice mode are displayed
      if (stepData.narration && stepData.narration.trim()) {
        setChatMessages((msgPrev) => {
          // Remove thinking message if it exists
          const filtered = msgPrev.filter((msg) => !(msg.isLoading && msg.type === "agent"));
          
          // Check if we already have this exact narration recently (within last 2 messages)
          // This prevents duplicate messages from the same step update
          const lastTwoMessages = filtered.slice(-2);
          const isDuplicate = lastTwoMessages.some(
            (msg) => msg.type === "agent" && msg.content === stepData.narration
          );
          
          if (isDuplicate) {
            return filtered; // Don't add duplicate
          }
          
          // Always add new message (don't update existing steps)
          // This ensures each agent response is preserved, even for step 1 after interruption
          return [
            ...filtered,
            {
              id: `agent-step-${stepData.step}-${Date.now()}`,
              type: "agent",
              content: stepData.narration,
              timestamp: new Date(),
            },
          ];
        });
      }
    },
    onError: (error) => {
      setVoiceStatus(`Error: ${error}`);
    },
    onAudioChunk: (audioBase64, sampleRate, numChannels) => {
      // Play audio chunk as it arrives
      playAudioChunk(audioBase64, sampleRate, numChannels);
    },
    onInterruption: () => {
      // Stop all audio when user interrupts
      stopAllAudio();
    },
  });

  // Update sendVoiceMessage ref
  useEffect(() => {
    sendVoiceMessageRef.current = sendVoiceMessage;
  }, [sendVoiceMessage]);
  
  // Clear steps when voice mode is toggled
  useEffect(() => {
    if (isVoiceMode && isVoiceConnected) {
      setSteps([]);
      setCurrentSlideIndex(0);
      // Don't reset hasNavigated - keep stream alive when switching modes
    }
  }, [isVoiceMode, isVoiceConnected]);

  // Close provider dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (providerDropdownRef.current && !providerDropdownRef.current.contains(event.target as Node)) {
        setIsProviderDropdownOpen(false);
      }
    };

    if (isProviderDropdownOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }

    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [isProviderDropdownOpen]);

  // Cleanup browser when tab closes
  useEffect(() => {
    const handleBeforeUnload = () => {
      // Stop voice pipeline if active and reset browser to blank page when frontend closes
      const blob = new Blob([JSON.stringify({})], { type: "application/json" });
      
      try {
        // Stop voice pipeline if voice mode is active
        if (isVoiceMode) {
          navigator.sendBeacon("/api/stop-voice", blob);
        }
        // Reset browser to blank page
        navigator.sendBeacon("/api/reset-browser", blob);
      } catch (e) {
        // Fallback if sendBeacon fails
        if (isVoiceMode) {
          fetch("/api/stop-voice", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
            keepalive: true,
          }).catch(() => {});
        }
        fetch("/api/reset-browser", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
          keepalive: true,
        }).catch(() => {});
      }
    };

    window.addEventListener("beforeunload", handleBeforeUnload);
    
    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload);
      // Also stop voice and reset browser on component unmount
      if (isVoiceMode) {
        fetch("/api/stop-voice", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
          keepalive: true,
        }).catch(() => {});
      }
      fetch("/api/reset-browser", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
        keepalive: true,
      }).catch(() => {});
    };
  }, [isVoiceMode]);

  const handleStopVoiceMode = () => {
    // Stop all audio
    stopAllAudio();
    
    // Disconnect voice WebSocket
    if (disconnectVoice) {
      disconnectVoice();
    }
    
    // Turn off voice mode
    setIsVoiceMode(false);
    
    // Remove thinking message and add "stopped by user" message
    setChatMessages((prev) => {
      const filtered = prev.filter((msg) => !(msg.isLoading && msg.type === "agent"));
      return [
        ...filtered,
        {
          id: `agent-stopped-${Date.now()}`,
          type: "agent",
          content: "Voice mode stopped by user",
          timestamp: new Date(),
        },
      ];
    });
  };

  const handleStop = () => {
    if (isVoiceMode) {
      // Stop voice mode processing
      if (disconnectVoice) {
        disconnectVoice();
      }
      
      // Remove thinking message and add "stopped by user" message
      setChatMessages((prev) => {
        const filtered = prev.filter((msg) => !(msg.isLoading && msg.type === "agent"));
        return [
          ...filtered,
          {
            id: `agent-stopped-${Date.now()}`,
            type: "agent",
            content: "Stopped by user",
            timestamp: new Date(),
          },
        ];
      });
    } else {
      // Abort the ongoing text request
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }
      
      // Cancel the reader if it exists
      if (readerRef.current) {
        readerRef.current.cancel().catch(() => {
          // Ignore cancellation errors
        });
        readerRef.current = null;
      }
      
      setIsLoading(false);
      
      // Remove thinking message and add "stopped by user" message
      setChatMessages((prev) => {
        const filtered = prev.filter((msg) => !(msg.isLoading && msg.type === "agent"));
        return [
          ...filtered,
          {
            id: `agent-stopped-${Date.now()}`,
            type: "agent",
            content: "Stopped by user",
            timestamp: new Date(),
          },
        ];
      });
    }
  };
  
  // Check if voice mode is processing (has thinking message or recent activity)
  const isVoiceProcessing = isVoiceMode && chatMessages.some(
    (msg) => msg.isLoading && msg.type === "agent"
  );
  
  // Determine if we should show stop button
  const shouldShowStop = !isVoiceMode ? isLoading : isVoiceProcessing;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() || isLoading) return;
    
    // In voice mode, don't submit via text - voice handles it
    if (isVoiceMode) {
      return;
    }

    const userQuery = query.trim();
    setIsSubmitted(true);
    setIsLoading(true);
    setQuery("");
    // Reset processed steps for new query
    setProcessedSteps(new Set());
    // Don't reset hasNavigated - keep stream alive once started

    // Add user query to chat and thinking message
    setChatMessages((prev) => [
      ...prev,
      {
        id: `user-${Date.now()}`,
        type: "user",
        content: userQuery,
        timestamp: new Date(),
      },
      {
        id: `thinking-${Date.now()}`,
        type: "agent",
        content: "Thinking...",
        timestamp: new Date(),
        isLoading: true,
      },
    ]);

    // Create new AbortController for this request
    const abortController = new AbortController();
    abortControllerRef.current = abortController;

    try {
      const response = await fetch("/api/query", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          query: userQuery,
          voiceMode: isVoiceMode,
          provider: provider,
        }),
        signal: abortController.signal,
      });

      if (!response.ok) {
        throw new Error("Failed to process query");
      }

      const reader = response.body?.getReader();
      if (!reader) {
        throw new Error("No response body");
      }

      readerRef.current = reader;
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6));
              if (data.type === "step") {
                setSteps((prev) => {
                  const newSteps = [
                    ...prev,
                    {
                      step: data.step,
                      narration: data.narration || "",
                      screenshot: data.screenshot,
                      timestamp: new Date(),
                    },
                  ];
                  // Auto-advance to the new slide
                  setCurrentSlideIndex(newSteps.length - 1);
                  return newSteps;
                });

                // Check if this step involves navigation
                if (data.tool && data.tool.toLowerCase().includes('navigate')) {
                  setHasNavigated(true);
                }
                
                // Add agent response to chat if there's narration
                if (data.narration) {
                  const stepKey = `${data.step}-${data.narration}`;
                  
                  setProcessedSteps((prev) => {
                    if (prev.has(stepKey)) {
                      return prev; // Already processed this step
                    }
                    const newSet = new Set(prev);
                    newSet.add(stepKey);
                    
                    // Add message to chat when we add to processed steps
                    setChatMessages((msgPrev) => {
                      // Remove thinking message if it exists
                      const filtered = msgPrev.filter((msg) => !(msg.isLoading && msg.type === "agent"));
                      
                      // Check if we already have this exact message (same content and step)
                      const messageExists = filtered.some(
                        (msg) => msg.type === "agent" && msg.content === data.narration && msg.id.startsWith(`agent-${data.step}-`)
                      );
                      
                      if (messageExists) {
                        return filtered; // Already have this message
                      }
                      
                      return [
                        ...filtered,
                        {
                          id: `agent-${data.step}-${Date.now()}`,
                          type: "agent",
                          content: data.narration,
                          timestamp: new Date(),
                        },
                      ];
                    });
                    
                    return newSet;
                  });
                }
              } else if (data.type === "complete") {
                // Query completed
              }
            } catch (e) {
              console.error("Failed to parse SSE data:", e);
            }
          }
        }
      }
    } catch (error: any) {
      // Ignore abort errors (user intentionally stopped)
      if (error.name === "AbortError") {
        return;
      }
      console.error("Error processing query:", error);
      // Remove thinking message on error
      setChatMessages((prev) => prev.filter((msg) => !(msg.isLoading && msg.type === "agent")));
    } finally {
      setIsLoading(false);
      abortControllerRef.current = null;
      readerRef.current = null;
    }
  };

  return (
    <div className={`${hasNavigated ? 'h-[calc(100vh-4rem)] overflow-hidden' : 'min-h-[calc(100vh-4rem)]'} bg-zinc-900 flex flex-col relative`}>
      <div className={`${hasNavigated ? 'max-w-full h-full' : 'max-w-6xl'} mx-auto w-full flex-1 flex flex-col ${isSubmitted || steps.length > 0 ? '' : 'justify-center'} ${hasNavigated ? 'px-0 lg:px-0' : 'px-4 md:px-8'} pt-4 md:pt-8 pb-0`}>
        {!isSubmitted && (
          <div className="flex flex-col items-center justify-center space-y-8 mb-8">
            <AnimatePresence>
              <motion.div
                key="subtitle"
                initial={{ opacity: 0, y: -20 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.4, ease: [0.4, 0, 0.2, 1] }}
                className="text-center"
              >
                <motion.p 
                  className=" text-4xl lg:text-5xl font-bold bg-gradient-to-r from-foreground to-foreground/70 bg-clip-text text-transparent mx-auto "
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ delay: 0.1 }}
                >
                  AI Voice Controlled Browser
                </motion.p>
                <motion.p 
                  className="text-lg text-muted-foreground max-w-2xl mx-auto mt-4"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ delay: 0.1 }}
                >
                  Control your browser with natural language
                </motion.p>
              </motion.div>
            </AnimatePresence>
            
            {/* Input form in center on initial load */}
            <motion.div
              layout
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ 
                duration: 0.6, 
                ease: [0.4, 0, 0.2, 1],
              }}
              className="w-full max-w-4xl"
            >
              <motion.form
                layout
                onSubmit={handleSubmit}
                className="space-y-4"
              >
                <Card className="border-0 bg-transparent rounded-full">
                  <CardContent className="p-4">
                    <div className="flex items-center gap-2 lg:gap-3">
                      {/* Provider Selector as Custom Dropdown */}
                      <div className="relative" ref={providerDropdownRef}>
                        <button
                          type="button"
                          onClick={() => setIsProviderDropdownOpen(!isProviderDropdownOpen)}
                          disabled={isLoading}
                          className="h-12 px-4 lg:px-8 rounded-full bg-zinc-900 border text-sm font-medium text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/30 transition-all flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          <span className="lg:hidden font-bold text-base">
                            {PROVIDER_INFO[provider].label.charAt(0)}
                          </span>
                          <span className="hidden lg:inline">
                            {PROVIDER_INFO[provider].label}
                          </span>
                          <ChevronDown className={`hidden lg:inline h-46 w-4 transition-transform ${isProviderDropdownOpen ? "rotate-180" : ""}`} />
                        </button>
                        <AnimatePresence>
                          {isProviderDropdownOpen && (
                            <motion.div
                              initial={{ opacity: 0, y: 10 }}
                              animate={{ opacity: 1, y: 0 }}
                              exit={{ opacity: 0, y: 10 }}
                              transition={{ duration: 0.2 }}
                              className="absolute bottom-full left-0 mb-2 w-full min-w-[100px] bg-zinc-900 border rounded-xl shadow-lg z-50 overflow-hidden"
                            >
                              {Object.entries(PROVIDER_INFO).map(([key, info]) => (
                                <button
                                  key={key}
                                  type="button"
                                  onClick={() => {
                                    setProvider(key as LLMProvider);
                                    setIsProviderDropdownOpen(false);
                                  }}
                                  disabled={isLoading}
                                  className={`w-full px-3 py-2.5 text-left text-sm font-medium transition-colors first:rounded-t-xl last:rounded-b-xl ${
                                    provider === key
                                      ? "bg-primary/20 text-primary"
                                      : "text-muted-foreground hover:bg-zinc-800"
                                  } disabled:opacity-50 disabled:cursor-not-allowed`}
                                >
                                  {info.label}
                                  {key === "gemini"}
                                  {key === "claude"}
                                  {key === "openai"}
                                </button>
                              ))}
                            </motion.div>
                          )}
                        </AnimatePresence>
                      </div>

                      {/* Input */}
                      <div className="flex-1 relative lg:ml-4 rounded-full">
                        <Input
                          type="text"
                          placeholder={
                            isVoiceMode 
                              ? isVoiceConnecting 
                                ? "Starting voice mode..." 
                                : isVoiceConnected 
                                  ? "Please talk..." 
                                  : "Voice mode disconnected"
                              : "Ask something..."
                          }
                          value={query}
                          onChange={(e) => setQuery(e.target.value)}
                          disabled={isLoading || isVoiceMode}
                          className="h-12 text-sm lg:text-base pr-12 border-2 focus-visible:ring-2 focus-visible:ring-primary/50 rounded-full placeholder:text-xs lg:placeholder:text-sm"
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' && !e.shiftKey && query.trim() && !isLoading) {
                              e.preventDefault();
                              handleSubmit(e as any);
                            }
                          }}
                        />
                      </div>

                      {/* Voice Mode Button / Stop Button */}
                      {isVoiceMode ? (
                        <Button
                          type="button"
                          variant="destructive"
                          size="icon"
                          onClick={handleStopVoiceMode}
                          disabled={isLoading}
                          className="h-12 w-12 shrink-0 rounded-full"
                        >
                          <Square className="h-5 w-5" />
                        </Button>
                      ) : (
                        <Button
                          type="button"
                          variant="outline"
                          size="icon"
                          onClick={() => setIsVoiceMode(true)}
                          disabled={isLoading}
                          className="h-12 w-12 shrink-0 rounded-full"
                        >
                          <Mic className="h-5 w-5" />
                        </Button>
                      )}

                      {/* Submit/Stop Button */}
                      {shouldShowStop ? (
                        <Button
                          type="button"
                          onClick={handleStop}
                          className="h-12 px-3 shrink-0 rounded-full"
                          size="lg"
                        >
                          <Square className="h-6 w-6" />
                        </Button>
                      ) : (
                        <Button
                          type="submit"
                          disabled={!query.trim() || isLoading}
                          className="h-12 px-3 shrink-0 rounded-full"
                          size="lg"
                        >
                          <Send className="h-6 w-6" />
                          
                        </Button>
                      )}
                    </div>
                  </CardContent>
                </Card>
              </motion.form>
            </motion.div>
          </div>
        )}

        {isSubmitted && (
          <div className={`flex-1 min-h-0 ${hasNavigated ? 'flex flex-col lg:flex-row gap-4 lg:h-full lg:overflow-hidden' : 'space-y-6 overflow-y-auto mb-4'}`}>
            {hasNavigated ? (
              <>
                {/* Left Column / Top Section - Browser Screen Stream */}
                <div className="w-full lg:w-2/3 flex-shrink-0 lg:pl-4 px-4 lg:px-0">
                  <div className="lg:sticky lg:top-4">
                    <BrowserScreenStream />
                  </div>
                </div>
                
                {/* Right Column / Bottom Section - Chat + Input */}
                <div className="w-full lg:w-1/3 flex flex-col lg:pr-4 px-4 lg:px-0 lg:h-full pb-8">
                  <div className="border-2 rounded-3xl p-0  flex flex-col lg:h-full h-auto lg:max-h-full max-h-[50vh]">
                    {/* Chat Conversation */}
                    <div className="flex-1 overflow-y-auto min-h-0">
                    <div className="min-h-full">
                      {chatMessages.length === 0 ? (
                        <div className="flex items-center justify-center h-full min-h-[400px]">
                          <motion.div
                            initial={{ opacity: 0, scale: 0.9 }}
                            animate={{ opacity: 1, scale: 1 }}
                            transition={{ duration: 0.3 }}
                            className="flex flex-col items-center justify-center gap-4"
                          >
                            {isLoading ? (
                              <>
                                <Loader2 className="h-10 w-10 animate-spin text-primary" />
                                <p className="text-sm text-muted-foreground animate-pulse">Processing your request...</p>
                              </>
                            ) : (
                              <p className="text-muted-foreground text-sm">
                                {isVoiceMode ? "Start speaking to begin the conversation..." : "Conversation will appear here"}
                              </p>
                            )}
                          </motion.div>
                        </div>
                      ) : (
                        <Chat messages={chatMessages} />
                      )}
                    </div>
                  </div>
                  
                  {/* Input form inline in right column / sticky on mobile */}
                  <div className="flex-shrink-0 lg:relative fixed bottom-0 left-0 right-0 lg:bottom-auto lg:left-auto lg:right-auto bg-zinc-800 lg:rounded-b-3xl p-4 lg:p-0 z-50">
                    <motion.form
                      layout
                      onSubmit={handleSubmit}
                      className="space-y-4"
                    >
                      <Card className="border-0 bg-transparent lg:bg-transparent backdrop-blur-sm rounded-full">
                        <CardContent className="p-4">
                          <div className="flex items-center gap-2 lg:gap-3">
                            {/* Voice Mode Button */}
                            {isVoiceMode ? (
                              <div className="flex items-center gap-2 ">
                                <div className={`h-2 w-2 rounded-full  ${
                                  isVoiceConnected ? 'bg-green-500 animate-pulse' : 
                                  isVoiceConnecting ? 'bg-yellow-500 animate-pulse' : 
                                  'bg-red-500'
                                }`} />
                              </div>
                            ) : null}
                            
                            {/* Input */}
                            <div className="flex-1 relative rounded-full">
                              <Input
                                type="text"
                                placeholder={
                                  isVoiceMode 
                                    ? isVoiceConnecting 
                                      ? "Starting voice mode..." 
                                      : isVoiceConnected 
                                        ? "Please talk..." 
                                        : "Voice mode disconnected"
                                    : "Ask something..."
                                }
                                value={query}
                                onChange={(e) => setQuery(e.target.value)}
                                disabled={isLoading || isVoiceMode}
                                className="h-12 text-sm lg:text-base pr-12 border-2 focus-visible:ring-2 focus-visible:ring-primary/50 rounded-full placeholder:text-xs lg:placeholder:text-sm"
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter' && !e.shiftKey && query.trim() && !isLoading) {
                                    e.preventDefault();
                                    handleSubmit(e as any);
                                  }
                                }}
                              />
                            </div>

                            {/* Voice Mode Button / Stop Button */}
                            {isVoiceMode ? (
                              <Button
                                type="button"
                                onClick={handleStopVoiceMode}
                                variant="destructive"
                                className="h-12 px-4 shrink-0 rounded-full"
                                size="lg"
                              >
                                <Mic className="h-5 w-5" />
                              </Button>
                            ) : (
                              <Button
                                type="button"
                                onClick={() => setIsVoiceMode(true)}
                                variant="ghost"
                                className="h-12 px-3 shrink-0 rounded-full border-2 border-white/30"
                                size="lg"
                              >
                                <Mic className="h-5 w-5" />
                              </Button>
                            )}

                            {/* Send / Stop Button */}
                            <div className="shrink-0">
                              {shouldShowStop ? (
                                <Button
                                  type="button"
                                  variant="destructive"
                                  onClick={handleStop}
                                  className="h-12 px-3 shrink-0 rounded-full"
                                  size="lg"
                                >
                                  <Square className="h-6 w-6" />
                                  {/* <span className="hidden lg:inline">Stop</span> */}
                                </Button>
                              ) : (
                                <Button
                                  type="submit"
                                  disabled={!query.trim() || isLoading || isVoiceMode}
                                  className="h-12 px-3 shrink-0 rounded-full"
                                  size="lg"
                                >
                                  <Send className="h-6 w-6" />
                                  {/* <span className="hidden lg:inline">Send</span> */}
                                </Button>
                              )}
                            </div>
                          </div>
                        </CardContent>
                      </Card>
                    </motion.form>
                  </div>
                  </div>
                </div>
              </>
            ) : (
              /* Single column layout when no navigation yet */
              <div className="flex-1">
                <div className={chatMessages.length === 0 ? "h-96" : "min-h-[400px]"}>
                  {chatMessages.length === 0 ? (
                    <div className="flex items-center justify-center h-full">
                      <motion.div
                        initial={{ opacity: 0, scale: 0.9 }}
                        animate={{ opacity: 1, scale: 1 }}
                        transition={{ duration: 0.3 }}
                        className="flex flex-col items-center justify-center gap-4"
                      >
                        {isLoading ? (
                          <>
                            <Loader2 className="h-10 w-10 animate-spin text-primary" />
                            <p className="text-sm text-muted-foreground animate-pulse">Processing your request...</p>
                          </>
                        ) : (
                          <p className="text-muted-foreground text-sm">
                            {isVoiceMode ? "Start speaking to begin the conversation..." : "Conversation will appear here"}
                          </p>
                        )}
                      </motion.div>
                    </div>
                  ) : (
                    <Chat messages={chatMessages} />
                  )}
                </div>
              </div>
            )}
          </div>
        )}

        {!isSubmitted && steps.length > 0 && (
          <div className="flex-1 overflow-y-auto min-h-0 mb-4">
            <AnimatePresence mode="popLayout">
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="space-y-4 mt-6"
              >
                {steps.map((stepData, index) => (
                  <motion.div
                    key={`${stepData.step}-${index}`}
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.3, delay: index * 0.1 }}
                    className="space-y-4"
                  >
                    {stepData.narration && (
                      <NarrationDisplay
                        step={stepData.step}
                        narration={stepData.narration}
                      />
                    )}
                    {stepData.screenshot && (
                      <ScreenshotDisplay
                        step={stepData.step}
                        screenshot={stepData.screenshot}
                      />
                    )}
                  </motion.div>
                ))}
              </motion.div>
            </AnimatePresence>
          </div>
        )}

        {!isSubmitted && isLoading && steps.length === 0 && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="flex items-center justify-center py-12"
          >
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </motion.div>
        )}
      </div>
      
      {/* Input form at bottom when submitted (only show if no navigation/stream) */}
      {isSubmitted && !hasNavigated && (
        <motion.div
          layout
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ 
            duration: 0.6, 
            ease: [0.4, 0, 0.2, 1],
          }}
          className="sticky bottom-0 z-50 bg-transparent"
        >
          <div className="max-w-6xl mx-auto px-4 md:px-8 py-4">
            <motion.form
              layout
              onSubmit={handleSubmit}
              className="space-y-4"
            >
              <Card className="border-0 bg-transparent  rounded-full">
                <CardContent className="p-4">
                  <div className="flex items-center gap-2 lg:gap-3">
                    {/* Provider Selector as Custom Dropdown */}
                    <div className="relative" ref={providerDropdownRef}>
                      <button
                        type="button"
                        onClick={() => setIsProviderDropdownOpen(!isProviderDropdownOpen)}
                        disabled={isLoading}
                        className="h-12 px-4 lg:px-8 rounded-full bg-zinc-900 border text-sm font-medium text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/30 transition-all flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        <span className="lg:hidden font-bold text-base">
                          {PROVIDER_INFO[provider].label.charAt(0)}
                        </span>
                        <span className="hidden lg:inline">
                          {PROVIDER_INFO[provider].label}
                        </span>
                        <ChevronDown className={`hidden lg:inline h-4 w-4 transition-transform ${isProviderDropdownOpen ? "rotate-180" : ""}`} />
                      </button>
                      <AnimatePresence>
                        {isProviderDropdownOpen && (
                          <motion.div
                            initial={{ opacity: 0, y: 10 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: 10 }}
                            transition={{ duration: 0.2 }}
                            className="absolute bottom-full left-0 mb-2 w-full min-w-[100px] bg-zinc-900 border rounded-xl shadow-lg z-50 overflow-hidden"
                          >
                            {Object.entries(PROVIDER_INFO).map(([key, info]) => (
                              <button
                                key={key}
                                type="button"
                                onClick={() => {
                                  setProvider(key as LLMProvider);
                                  setIsProviderDropdownOpen(false);
                                }}
                                disabled={isLoading}
                                className={`w-full px-3 py-2.5 text-left text-sm font-medium transition-colors first:rounded-t-xl last:rounded-b-xl ${
                                  provider === key
                                    ? "bg-primary/20 text-primary"
                                    : "text-muted-foreground hover:bg-zinc-800"
                                } disabled:opacity-50 disabled:cursor-not-allowed`}
                              >
                                {info.label}
                                {key === "gemini" }
                                {key === "claude" }
                                {key === "openai" }
                              </button>
                            ))}
                          </motion.div>
                        )}
                      </AnimatePresence>
                    </div>

                    {/* Input */}
                    <div className="flex-1 relative lg:ml-4 rounded-full">
                      <Input
                        type="text"
                        placeholder={
                          isVoiceMode 
                            ? isVoiceConnecting 
                              ? "Starting voice mode..." 
                              : isVoiceConnected 
                                ? "Please talk..." 
                                : "Voice mode disconnected"
                            : "Ask something..."
                        }
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        disabled={isLoading || isVoiceMode}
                        className="h-12 text-sm lg:text-base pr-12 border-2 focus-visible:ring-2 focus-visible:ring-primary/50 rounded-full placeholder:text-xs lg:placeholder:text-sm"
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' && !e.shiftKey && query.trim() && !isLoading) {
                            e.preventDefault();
                            handleSubmit(e as any);
                          }
                        }}
                      />
                    </div>

                    {/* Voice Mode Button / Stop Button */}
                    {isVoiceMode ? (
                      <Button
                        type="button"
                        variant="destructive"
                        size="icon"
                        onClick={handleStopVoiceMode}
                        disabled={isLoading}
                        className="h-12 w-12 shrink-0 rounded-full"
                      >
                        <Square className="h-5 w-5" />
                      </Button>
                    ) : (
                      <Button
                        type="button"
                        variant="outline"
                        size="icon"
                        onClick={() => setIsVoiceMode(true)}
                        disabled={isLoading}
                        className="h-12 w-12 shrink-0 rounded-full"
                      >
                        <Mic className="h-5 w-5" />
                      </Button>
                    )}

                    {/* Submit/Stop Button */}
                    {shouldShowStop ? (
                      <Button
                        type="button"
                        onClick={handleStop}
                        className="h-12 px-3 shrink-0 rounded-full"
                        size="lg"
                      >
                        <Square className="h-6 w-6" />
                      </Button>
                    ) : (
                      <Button
                        type="submit"
                        disabled={!query.trim() || isLoading}
                        className="h-12 px-3 shrink-0 rounded-full"
                        size="lg"
                      >
                        <Send className="h-6 w-6" />
                      </Button>
                    )}
                  </div>
                </CardContent>
              </Card>
            </motion.form>
          </div>
        </motion.div>
      )}
    </div>
  );
}

