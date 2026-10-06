import { useCallback, useRef, useState } from "react";
import { api } from "../services/api";

export type TtsSpeaker = "patient" | "tutor";

export function useFlashTts() {
  const queueRef = useRef<Promise<void>>(Promise.resolve());
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const cancelledRef = useRef(false);
  const [isSpeaking, setIsSpeaking] = useState(false);

  const stop = useCallback(() => {
    cancelledRef.current = true;
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = "";
      audioRef.current = null;
    }
    setIsSpeaking(false);
    queueRef.current = Promise.resolve();
  }, []);

  const speak = useCallback((text: string, speaker: TtsSpeaker = "patient") => {
    const trimmed = text.trim();
    if (!trimmed) return;
    cancelledRef.current = false;

    queueRef.current = queueRef.current.then(async () => {
      if (cancelledRef.current) return;
      setIsSpeaking(true);
      try {
        const blob = await api.voice.tts(trimmed, speaker);
        if (cancelledRef.current) return;
        const url = URL.createObjectURL(blob);
        await new Promise<void>((resolve) => {
          const audio = new Audio(url);
          audioRef.current = audio;
          audio.onended = () => {
            URL.revokeObjectURL(url);
            resolve();
          };
          audio.onerror = () => {
            URL.revokeObjectURL(url);
            resolve();
          };
          audio.play().catch(() => resolve());
        });
      } catch {
        // TTS failure should not block the dialogue UI
      } finally {
        setIsSpeaking(false);
      }
    });
  }, []);

  return { speak, stop, isSpeaking };
}
