import { useCallback, useEffect, useRef, useState } from "react";

import {
  getVoiceCapabilities,
  synthesizeSpeech,
  transcribeAudio,
} from "../api";
import type { VoiceCapabilities } from "../types";

interface VoiceOptions {
  onTranscription: (text: string) => void;
  onError: (message: string) => void;
}

const AUTO_SPEAK_KEY = "human-chat:auto-speak";

export function useVoice({ onTranscription, onError }: VoiceOptions) {
  const [capabilities, setCapabilities] =
    useState<VoiceCapabilities | null>(null);
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [speechLoadingId, setSpeechLoadingId] = useState<string | null>(null);
  const [speakingId, setSpeakingId] = useState<string | null>(null);
  const [autoSpeak, setAutoSpeak] = useState(
    () => localStorage.getItem(AUTO_SPEAK_KEY) === "true",
  );

  const onTranscriptionRef = useRef(onTranscription);
  const onErrorRef = useRef(onError);
  const autoSpeakRef = useRef(autoSpeak);
  const ttsEnabledRef = useRef(false);
  const mountedRef = useRef(true);
  const startingRecordingRef = useRef(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const recorderStreamRef = useRef<MediaStream | null>(null);
  const recorderChunksRef = useRef<Blob[]>([]);
  const transcriptionControllerRef = useRef<AbortController | null>(null);
  const speechControllerRef = useRef<AbortController | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioUrlRef = useRef<string | null>(null);

  useEffect(() => {
    onTranscriptionRef.current = onTranscription;
    onErrorRef.current = onError;
  }, [onError, onTranscription]);

  useEffect(() => {
    autoSpeakRef.current = autoSpeak;
    localStorage.setItem(AUTO_SPEAK_KEY, String(autoSpeak));
  }, [autoSpeak]);

  useEffect(() => {
    const controller = new AbortController();
    getVoiceCapabilities(controller.signal)
      .then(setCapabilities)
      .catch((reason: unknown) => {
        if (!isAbortError(reason)) {
          // Voice is optional. Failure to discover it must not interrupt text chat.
          setCapabilities(null);
        }
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    ttsEnabledRef.current = capabilities?.tts_enabled === true;
  }, [capabilities?.tts_enabled]);

  const stopPlayback = useCallback(() => {
    speechControllerRef.current?.abort();
    speechControllerRef.current = null;
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = "";
      audioRef.current = null;
    }
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current);
      audioUrlRef.current = null;
    }
    setSpeechLoadingId(null);
    setSpeakingId(null);
  }, []);

  const playSpeech = useCallback(
    async (messageId: string, text: string) => {
      stopPlayback();
      const controller = new AbortController();
      speechControllerRef.current = controller;
      setSpeechLoadingId(messageId);
      try {
        const blob = await synthesizeSpeech(text, controller.signal);
        if (controller.signal.aborted) {
          return;
        }
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        audioRef.current = audio;
        audioUrlRef.current = url;
        audio.onended = stopPlayback;
        audio.onerror = () => {
          stopPlayback();
          onErrorRef.current("语音播放失败。");
        };
        setSpeechLoadingId(null);
        setSpeakingId(messageId);
        await audio.play();
      } catch (reason) {
        if (!isAbortError(reason)) {
          stopPlayback();
          onErrorRef.current(errorMessage(reason));
        }
      } finally {
        if (speechControllerRef.current === controller) {
          speechControllerRef.current = null;
        }
      }
    },
    [stopPlayback],
  );

  const toggleSpeech = useCallback(
    (messageId: string, text: string) => {
      if (speakingId === messageId || speechLoadingId === messageId) {
        stopPlayback();
        return;
      }
      void playSpeech(messageId, text);
    },
    [playSpeech, speakingId, speechLoadingId, stopPlayback],
  );

  const speakAutomatically = useCallback(
    (messageId: string, text: string) => {
      if (autoSpeakRef.current && ttsEnabledRef.current) {
        void playSpeech(messageId, text);
      }
    },
    [playSpeech],
  );

  const uploadForTranscription = useCallback(
    async (audio: Blob, filename: string) => {
      const maximum = capabilities?.max_audio_bytes;
      if (maximum && audio.size > maximum) {
        onErrorRef.current("音频文件超过大小限制。");
        return;
      }

      transcriptionControllerRef.current?.abort();
      const controller = new AbortController();
      transcriptionControllerRef.current = controller;
      setTranscribing(true);
      try {
        const result = await transcribeAudio(audio, filename, controller.signal);
        onTranscriptionRef.current(result.text);
      } catch (reason) {
        if (!isAbortError(reason)) {
          onErrorRef.current(errorMessage(reason));
        }
      } finally {
        if (transcriptionControllerRef.current === controller) {
          transcriptionControllerRef.current = null;
          setTranscribing(false);
        }
      }
    },
    [capabilities?.max_audio_bytes],
  );

  const transcribeFile = useCallback(
    (file: File) => {
      const contentType = file.type || audioTypeFromFilename(file.name);
      if (contentType === "application/octet-stream") {
        onErrorRef.current("无法识别该文件的音频格式。");
        return;
      }
      const upload = file.type
        ? file
        : new File([file], file.name, { type: contentType });
      void uploadForTranscription(upload, upload.name);
    },
    [uploadForTranscription],
  );

  const stopRecording = useCallback(() => {
    const recorder = recorderRef.current;
    if (recorder?.state === "recording") {
      recorder.stop();
    }
  }, []);

  const startRecording = useCallback(async () => {
    if (startingRecordingRef.current) {
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || !("MediaRecorder" in window)) {
      onErrorRef.current("当前浏览器不支持麦克风录音。");
      return;
    }
    startingRecordingRef.current = true;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!mountedRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      const mimeType = preferredRecordingType();
      const recorder = new MediaRecorder(
        stream,
        mimeType ? { mimeType } : undefined,
      );
      recorderRef.current = recorder;
      recorderStreamRef.current = stream;
      recorderChunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          recorderChunksRef.current.push(event.data);
        }
      };
      recorder.onerror = () => {
        releaseRecorder();
        if (mountedRef.current) {
          setRecording(false);
          onErrorRef.current("录音失败。");
        }
      };
      recorder.onstop = () => {
        const type = recorder.mimeType || mimeType || "audio/webm";
        const blob = new Blob(recorderChunksRef.current, { type });
        releaseRecorder();
        if (!mountedRef.current) {
          return;
        }
        setRecording(false);
        if (blob.size > 0) {
          void uploadForTranscription(blob, recordingFilename(type));
        }
      };
      recorder.start(250);
      setRecording(true);
    } catch (reason) {
      releaseRecorder();
      setRecording(false);
      const message =
        reason instanceof DOMException && reason.name === "NotAllowedError"
          ? "没有获得麦克风权限。"
          : errorMessage(reason);
      onErrorRef.current(message);
    } finally {
      startingRecordingRef.current = false;
    }
  }, [uploadForTranscription]);

  useEffect(
    () => {
      mountedRef.current = true;
      return () => {
        mountedRef.current = false;
        recorderRef.current?.state === "recording" && recorderRef.current.stop();
        releaseRecorder();
        transcriptionControllerRef.current?.abort();
        stopPlayback();
      };
    },
    [stopPlayback],
  );

  return {
    capabilities,
    recording,
    transcribing,
    speechLoadingId,
    speakingId,
    autoSpeak,
    toggleAutoSpeak: () => setAutoSpeak((current) => !current),
    toggleRecording: () => (recording ? stopRecording() : void startRecording()),
    transcribeFile,
    toggleSpeech,
    speakAutomatically,
  };

  function releaseRecorder() {
    recorderStreamRef.current?.getTracks().forEach((track) => track.stop());
    recorderStreamRef.current = null;
    recorderRef.current = null;
    recorderChunksRef.current = [];
  }
}

function preferredRecordingType(): string {
  const types = [
    "audio/webm;codecs=opus",
    "audio/mp4",
    "audio/ogg;codecs=opus",
    "audio/webm",
  ];
  return types.find((type) => MediaRecorder.isTypeSupported(type)) || "";
}

function recordingFilename(contentType: string): string {
  if (contentType.includes("mp4")) return "recording.m4a";
  if (contentType.includes("ogg")) return "recording.ogg";
  return "recording.webm";
}

function audioTypeFromFilename(filename: string): string {
  const extension = filename.toLowerCase().split(".").pop();
  const types: Record<string, string> = {
    flac: "audio/flac",
    m4a: "audio/mp4",
    mp3: "audio/mpeg",
    mp4: "audio/mp4",
    ogg: "audio/ogg",
    wav: "audio/wav",
    webm: "audio/webm",
  };
  return types[extension || ""] || "application/octet-stream";
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : "语音请求未能完成。";
}

function isAbortError(reason: unknown): boolean {
  return reason instanceof DOMException && reason.name === "AbortError";
}
