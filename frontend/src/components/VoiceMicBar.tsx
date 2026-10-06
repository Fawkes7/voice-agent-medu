import { useCallback, useEffect } from "react";
import { useScribeRealtime } from "../hooks/useScribeRealtime";

interface Props {
  enabled: boolean;
  disabled: boolean;
  ttsPlaying?: boolean;
  languageCode?: string;
  onUtterance: (text: string) => void;
}

export default function VoiceMicBar({
  enabled,
  disabled,
  ttsPlaying = false,
  languageCode = "zh",
  onUtterance,
}: Props) {
  const scribe = useScribeRealtime(onUtterance, languageCode);
  const ignoreInput = disabled || ttsPlaying;

  useEffect(() => {
    scribe.setIgnoreCommits(ignoreInput);
  }, [ignoreInput, scribe]);

  useEffect(() => {
    return () => scribe.stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggle = useCallback(() => {
    if (scribe.isListening) {
      scribe.stop();
      return;
    }
    void scribe.start();
  }, [scribe]);

  if (!enabled) {
    return (
      <div className="px-4 py-2 bg-amber-50 border-t border-amber-200 text-xs text-amber-800">
        未配置 ElevenLabs API Key，语音问诊不可用。请在 backend/.env 中设置 ELEVENLABS_API_KEY。文字输入仍可使用。
      </div>
    );
  }

  return (
    <div className="border-t border-slate-200 bg-white px-4 py-3">
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={toggle}
          disabled={disabled && !scribe.isListening}
          className={`relative w-12 h-12 rounded-full flex items-center justify-center text-white transition-colors disabled:opacity-40 ${
            scribe.isListening ? "bg-red-500 hover:bg-red-600" : "bg-medical hover:bg-medical-dark"
          }`}
          title={scribe.isListening ? "停止听写" : "开始语音问诊"}
        >
          {scribe.status === "listening" && (
            <span className="absolute inset-0 rounded-full bg-red-400 animate-ping opacity-30" />
          )}
          <svg viewBox="0 0 24 24" className="w-5 h-5 relative" fill="currentColor" aria-hidden>
            <path d="M12 14a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v5a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2z" />
          </svg>
        </button>
        <div className="flex-1 min-w-0">
          <div className="text-xs font-medium text-slate-500">
            {scribe.status === "connecting"
              ? "正在连接 Scribe v2 Realtime…"
              : scribe.status === "listening"
                ? ttsPlaying
                  ? "患者正在说话，稍后再问…"
                  : "正在聆听中文提问 · Scribe v2 Realtime"
                : "中文语音问诊（点击麦克风开始）"}
          </div>
          <div className="text-sm text-slate-700 truncate min-h-[1.25rem]">
            {scribe.partial ||
              (scribe.status === "listening"
                ? "请用中文提问，说完一句会自动发送…"
                : "默认中文识别；说完一句话后自动发送，也可用文字补充。")}
          </div>
        </div>
      </div>
      {scribe.error && <p className="mt-2 text-xs text-red-600">{scribe.error}</p>}
    </div>
  );
}
