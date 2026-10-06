import { useCallback, useRef, useState } from "react";
import { api } from "../services/api";

const SAMPLE_RATE = 16000;

const KEYTERMS = [
  "阑尾炎",
  "急性阑尾炎",
  "胆囊炎",
  "急性胆囊炎",
  "胰腺炎",
  "急性胰腺炎",
  "肠梗阻",
  "肠系膜缺血",
  "肠系膜动脉栓塞",
  "消化性溃疡",
  "消化性溃疡穿孔",
  "主诉",
  "现病史",
  "既往史",
  "鉴别诊断",
  "转移性右下腹痛",
  "麦氏点",
  "反跳痛",
  "墨菲征",
];

function floatTo16BitPCM(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

function downsample(buffer: Float32Array, inRate: number, outRate: number): Float32Array {
  if (inRate === outRate) return buffer;
  const ratio = inRate / outRate;
  const newLen = Math.round(buffer.length / ratio);
  const result = new Float32Array(newLen);
  for (let i = 0; i < newLen; i++) {
    result[i] = buffer[Math.min(buffer.length - 1, Math.floor(i * ratio))];
  }
  return result;
}

function uint8ToBase64(bytes: Uint8Array): string {
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

export function useScribeRealtime(
  onUtterance: (text: string) => void,
  languageCode = "zh"
) {
  const [status, setStatus] = useState<"idle" | "connecting" | "listening" | "error">("idle");
  const [partial, setPartial] = useState("");
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const ignoreCommitRef = useRef(false);
  const onUtteranceRef = useRef(onUtterance);
  onUtteranceRef.current = onUtterance;

  const stop = useCallback(() => {
    if (processorRef.current) {
      processorRef.current.disconnect();
      processorRef.current = null;
    }
    if (audioCtxRef.current) {
      void audioCtxRef.current.close();
      audioCtxRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (wsRef.current) {
      try {
        wsRef.current.close();
      } catch {
        // ignore
      }
      wsRef.current = null;
    }
    setPartial("");
    setStatus("idle");
  }, []);

  const start = useCallback(async () => {
    setError(null);
    setStatus("connecting");
    try {
      const { token } = await api.voice.scribeToken();
      const params = new URLSearchParams({
        model_id: "scribe_v2_realtime",
        token,
        audio_format: "pcm_16000",
        commit_strategy: "vad",
        language_code: languageCode || "zh",
        vad_silence_threshold_secs: "0.8",
        no_verbatim: "true",
        filter_background_audio: "true",
      });
      params.append("secondary_languages", "en");
      for (const term of KEYTERMS) {
        params.append("keyterms", term);
      }

      const ws = new WebSocket(
        `wss://api.elevenlabs.io/v1/speech-to-text/realtime?${params.toString()}`
      );
      wsRef.current = ws;

      ws.onmessage = (event) => {
        let data: { message_type?: string; text?: string; error?: string };
        try {
          data = JSON.parse(event.data);
        } catch {
          return;
        }
        if (data.message_type === "partial_transcript") {
          setPartial(data.text || "");
        } else if (data.message_type === "committed_transcript") {
          setPartial("");
          const text = (data.text || "").trim();
          if (text && !ignoreCommitRef.current) {
            onUtteranceRef.current(text);
          }
        } else if (
          data.message_type === "error" ||
          data.message_type === "auth_error" ||
          data.message_type === "quota_exceeded"
        ) {
          setError(data.error || data.message_type || "语音识别失败");
          setStatus("error");
        }
      };

      ws.onerror = () => {
        setError("Scribe WebSocket 连接失败");
        setStatus("error");
      };

      ws.onclose = () => {
        if (wsRef.current === ws) {
          stop();
        }
      };

      await new Promise<void>((resolve, reject) => {
        const timer = window.setTimeout(() => reject(new Error("Scribe 连接超时")), 10000);
        ws.onopen = () => {
          window.clearTimeout(timer);
          resolve();
        };
      });

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        },
      });
      streamRef.current = stream;

      const audioCtx = new AudioContext();
      audioCtxRef.current = audioCtx;
      const source = audioCtx.createMediaStreamSource(stream);
      const processor = audioCtx.createScriptProcessor(4096, 1, 1);
      processorRef.current = processor;

      processor.onaudioprocess = (event) => {
        if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
        const input = event.inputBuffer.getChannelData(0);
        const resampled = downsample(input, audioCtx.sampleRate, SAMPLE_RATE);
        const pcm = floatTo16BitPCM(resampled);
        const audio_base_64 = uint8ToBase64(new Uint8Array(pcm.buffer));
        wsRef.current.send(
          JSON.stringify({
            message_type: "input_audio_chunk",
            audio_base_64,
            commit: false,
            sample_rate: SAMPLE_RATE,
          })
        );
      };

      source.connect(processor);
      const mute = audioCtx.createGain();
      mute.gain.value = 0;
      processor.connect(mute);
      mute.connect(audioCtx.destination);
      if (audioCtx.state === "suspended") {
        await audioCtx.resume();
      }
      setStatus("listening");
    } catch (err) {
      stop();
      setError(err instanceof Error ? err.message : "无法启动语音识别");
      setStatus("error");
    }
  }, [stop, languageCode]);

  const setIgnoreCommits = useCallback((ignore: boolean) => {
    ignoreCommitRef.current = ignore;
  }, []);

  return {
    status,
    partial,
    error,
    start,
    stop,
    setIgnoreCommits,
    isListening: status === "listening" || status === "connecting",
  };
}
