import { NextRequest, NextResponse } from "next/server";
import { getBackendUrl } from "@/lib/config";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { query, voiceMode, provider } = body;

    if (!query) {
      return NextResponse.json(
        { error: "Query is required" },
        { status: 400 }
      );
    }

    // Create a readable stream for Server-Sent Events
    const encoder = new TextEncoder();
    
    // Create abort controller in outer scope so cancel() can access it
    const backendAbortController = new AbortController();
    let backendReader: ReadableStreamDefaultReader<Uint8Array> | null = null;
    
    const stream = new ReadableStream({
      async start(controller) {
        try {
          const backendUrl = getBackendUrl();
          
          // Call the Python FastAPI backend with abort signal
          const response = await fetch(`${backendUrl}/api/query`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ query, voiceMode, provider }),
            signal: backendAbortController.signal,
          });

          if (!response.ok) {
            throw new Error(`Backend returned ${response.status}`);
          }

          // Stream the response from backend
          backendReader = response.body?.getReader();
          if (!backendReader) {
            throw new Error("No response body");
          }

          const decoder = new TextDecoder();
          let buffer = "";

          while (true) {
            const { done, value } = await backendReader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop() || "";

            for (const line of lines) {
              if (line.startsWith("data: ")) {
                controller.enqueue(encoder.encode(line + "\n"));
              }
            }
          }

          controller.close();
        } catch (error) {
          if (error instanceof Error && error.name === "AbortError") {
            console.log("Backend request aborted by client disconnect");
          } else {
            const errorData = JSON.stringify({
              type: "error",
              error: error instanceof Error ? error.message : "Unknown error",
            });
            controller.enqueue(encoder.encode(`data: ${errorData}\n\n`));
          }
          controller.close();
        }
      },
      cancel() {
        // This is called when the client disconnects
        console.log("Client disconnected, aborting backend request");
        backendAbortController.abort();
        if (backendReader) {
          backendReader.cancel().catch(() => {
            // Ignore errors when cancelling reader
          });
        }
      }
    });

    return new Response(stream, {
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      },
    });
  } catch (error) {
    return NextResponse.json(
      {
        error:
          error instanceof Error ? error.message : "Failed to process query",
      },
      { status: 500 }
    );
  }
}

