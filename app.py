import os
import json
from tkinter import (
    Tk,
    Label,
    Button,
    Entry,
    StringVar,
    IntVar,
    BooleanVar,
    Text,
    END,
    DISABLED,
    NORMAL,
    filedialog,
    messagebox,
)

try:
    from dotenv import load_dotenv
    load_dotenv()  # Load .env file if it exists
except ImportError:
    pass  # python-dotenv not installed, environment variables must be set manually

try:
    # GUI widgets (nice themed ones) if available
    from tkinter import ttk
except ImportError:  # pragma: no cover
    ttk = None

moviepy_import_error = None
try:
    from moviepy.editor import VideoFileClip, concatenate_videoclips, CompositeVideoClip, ImageClip
except Exception as e:  # pragma: no cover
    # Store the original import error so we can show it in the GUI
    moviepy_import_error = e
    VideoFileClip = None
    concatenate_videoclips = None
    CompositeVideoClip = None
    ImageClip = None

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:  # pragma: no cover
    Image = None
    ImageDraw = None
    ImageFont = None
    PIL_AVAILABLE = False

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None

try:
    import google.generativeai as genai
except ImportError:  # pragma: no cover
    genai = None


class VideoProcessor:
    """Helpers for splitting videos and adding intro/outro and logo overlay."""

    @staticmethod
    def _get_logo_position(position: str):
        mapping = {
            "top-left": ("left", "top"),
            "top-right": ("right", "top"),
            "bottom-left": ("left", "bottom"),
            "bottom-right": ("right", "bottom"),
        }
        return mapping.get(position, ("right", "bottom"))

    @staticmethod
    def extract_audio(input_path: str, output_audio_path: str) -> str:
        """Extract audio from video and save as .mp3 for transcription.

        Returns the path to the audio file.
        """
        if VideoFileClip is None:
            raise RuntimeError(
                f"MoviePy could not be imported. "
                f"Details: {moviepy_import_error!r}"
            )

        with VideoFileClip(input_path) as clip:
            if clip.audio is None:
                raise ValueError("Video has no audio track.")
            # Use lower bitrate to keep file under 25MB for Whisper API
            clip.audio.write_audiofile(
                output_audio_path,
                bitrate="32k",  # Lower bitrate = smaller file
                verbose=False,
                logger=None
            )

        return output_audio_path

    @staticmethod
    def split_video(
        input_path: str,
        output_dir: str,
        clip_length_seconds: int,
        intro_path: str | None = None,
        outro_path: str | None = None,
        logo_path: str | None = None,
        logo_position: str = "bottom-right",
        output_prefix: str = "clip",
    ) -> list[str]:
        if VideoFileClip is None:
            raise RuntimeError(
                f"MoviePy could not be imported. "
                f"Details: {moviepy_import_error!r}"
            )

        if not os.path.isfile(input_path):
            raise FileNotFoundError(f"Input video not found: {input_path}")

        os.makedirs(output_dir, exist_ok=True)

        clips_created: list[str] = []

        with VideoFileClip(input_path) as main_clip:
            duration = main_clip.duration or 0
            if duration <= 0:
                raise ValueError("Could not determine video duration.")

            intro_clip = VideoFileClip(intro_path) if intro_path else None
            outro_clip = VideoFileClip(outro_path) if outro_path else None

            # Loop over the main video and cut into chunks
            clip_index = 1
            start = 0.0
            while start < duration:
                end = min(start + clip_length_seconds, duration)
                subclip = main_clip.subclip(start, end)

                pieces = []
                if intro_clip is not None:
                    pieces.append(intro_clip)
                pieces.append(subclip)
                if outro_clip is not None:
                    pieces.append(outro_clip)

                if len(pieces) > 1:
                    final_clip = concatenate_videoclips(pieces)
                else:
                    final_clip = pieces[0]

                # Add logo overlay if provided
                if logo_path and os.path.isfile(logo_path) and ImageClip is not None:
                    logo = (
                        ImageClip(logo_path)
                        .set_duration(final_clip.duration)
                        .set_pos(VideoProcessor._get_logo_position(logo_position))
                    )
                    final_with_logo = CompositeVideoClip([final_clip, logo])
                else:
                    final_with_logo = final_clip

                output_filename = f"{output_prefix}_{clip_index:03d}.mp4"
                output_path = os.path.join(output_dir, output_filename)

                # Export clip
                final_with_logo.write_videofile(
                    output_path,
                    codec="libx264",
                    audio_codec="aac",
                    fps=final_with_logo.fps or 25,
                    verbose=False,
                    logger=None,
                )

                clips_created.append(output_path)
                clip_index += 1
                start += clip_length_seconds

            if intro_clip is not None:
                intro_clip.close()
            if outro_clip is not None:
                outro_clip.close()

        return clips_created

    @staticmethod
    def create_smart_clips(
        input_path: str,
        output_dir: str,
        clip_specs: list[dict],
        intro_path: str | None = None,
        outro_path: str | None = None,
        logo_path: str | None = None,
        logo_position: str = "bottom-right",
    ) -> list[dict]:
        """Create clips based on AI-identified time ranges.

        Each item in clip_specs should have:
          - start_time (seconds)
          - end_time (seconds)
          - title, description, thumbnail_idea (optional metadata)

        Returns a list of dicts with 'path' and the original metadata.
        """
        if VideoFileClip is None:
            raise RuntimeError(
                f"MoviePy could not be imported. "
                f"Details: {moviepy_import_error!r}"
            )

        if not os.path.isfile(input_path):
            raise FileNotFoundError(f"Input video not found: {input_path}")

        os.makedirs(output_dir, exist_ok=True)

        clips_created: list[dict] = []

        with VideoFileClip(input_path) as main_clip:
            intro_clip = VideoFileClip(intro_path) if intro_path else None
            outro_clip = VideoFileClip(outro_path) if outro_path else None

            for idx, spec in enumerate(clip_specs, start=1):
                start_time = float(spec.get("start_time", 0))
                end_time = float(spec.get("end_time", 0))
                if end_time <= start_time:
                    continue

                subclip = main_clip.subclip(start_time, end_time)

                pieces = []
                if intro_clip is not None:
                    pieces.append(intro_clip)
                pieces.append(subclip)
                if outro_clip is not None:
                    pieces.append(outro_clip)

                if len(pieces) > 1:
                    final_clip = concatenate_videoclips(pieces)
                else:
                    final_clip = pieces[0]

                # Add logo overlay if provided
                if logo_path and os.path.isfile(logo_path) and ImageClip is not None:
                    logo = (
                        ImageClip(logo_path)
                        .set_duration(final_clip.duration)
                        .set_pos(VideoProcessor._get_logo_position(logo_position))
                    )
                    final_with_logo = CompositeVideoClip([final_clip, logo])
                else:
                    final_with_logo = final_clip

                # Use title for filename (sanitized)
                title = spec.get("title", f"clip_{idx}")
                safe_title = "".join(c if c.isalnum() or c in " _-" else "_" for c in title)
                safe_title = safe_title[:50]  # limit length
                output_filename = f"{idx:03d}_{safe_title}.mp4"
                output_path = os.path.join(output_dir, output_filename)

                # Export clip
                final_with_logo.write_videofile(
                    output_path,
                    codec="libx264",
                    audio_codec="aac",
                    fps=final_with_logo.fps or 25,
                    verbose=False,
                    logger=None,
                )

                # Store clip info
                clip_info = {
                    "path": output_path,
                    "start_time": start_time,
                    "end_time": end_time,
                    "title": spec.get("title", ""),
                    "description": spec.get("description", ""),
                    "thumbnail_idea": spec.get("thumbnail_idea", ""),
                }
                clips_created.append(clip_info)

            if intro_clip is not None:
                intro_clip.close()
            if outro_clip is not None:
                outro_clip.close()

        return clips_created

    @staticmethod
    def add_subtitles_to_video(
        video_path: str,
        output_path: str,
        transcript_segments: list[dict]
    ) -> None:
        """Add burned-in subtitles to a video.
        
        Args:
            video_path: Path to input video
            output_path: Path to save video with subtitles
            transcript_segments: List of dicts with 'start', 'end', 'text' keys
        """
        if VideoFileClip is None:
            raise RuntimeError("MoviePy is required for subtitle generation")
        
        try:
            from moviepy.video.tools.subtitles import SubtitlesClip
            from moviepy.video.VideoClip import TextClip
        except ImportError:
            raise RuntimeError(
                "MoviePy subtitle modules not available. "
                "Make sure you have moviepy<2 installed."
            )
        
        # Create subtitle function for SubtitlesClip
        def generator(txt):
            """Generate a TextClip for each subtitle."""
            return TextClip(
                txt,
                font='Arial-Bold',
                fontsize=36,
                color='white',
                bg_color='black',
                size=(None, None),
                method='caption'
            )
        
        # Convert transcript segments to subtitle format
        # Format: [(start_time, end_time, text), ...]
        subtitle_data = []
        for seg in transcript_segments:
            start = float(seg.get('start', 0))
            end = float(seg.get('end', 0))
            text = str(seg.get('text', '')).strip()
            if text and end > start:
                subtitle_data.append(((start, end), text))
        
        if not subtitle_data:
            # No subtitles to add, just copy the video
            import shutil
            shutil.copy2(video_path, output_path)
            return
        
        # Load video and add subtitles
        with VideoFileClip(video_path) as video:
            # Create subtitle clip
            subtitles = SubtitlesClip(subtitle_data, generator)
            
            # Position subtitles at bottom center
            subtitles = subtitles.set_position(('center', 'bottom'))
            
            # Composite video with subtitles
            final = CompositeVideoClip([video, subtitles])
            
            # Write output
            final.write_videofile(
                output_path,
                codec='libx264',
                audio_codec='aac',
                fps=video.fps or 25,
                verbose=False,
                logger=None
            )


class AIHelper:
    """Wrapper around Google Gemini API for AI-powered features."""

    def __init__(self) -> None:
        # Try Gemini first, fallback to OpenAI
        gemini_key = os.getenv("GEMINI_API_KEY")
        openai_key = os.getenv("OPENAI_API_KEY")
        
        self.use_gemini = False
        self.gemini_model = None
        self.openai_client = None
        
        if genai is not None and gemini_key:
            # Use Gemini 3 (latest model)
            genai.configure(api_key=gemini_key)
            self.gemini_model = genai.GenerativeModel('gemini-3-flash-preview')  # Gemini 3.0 Flash
            self.use_gemini = True
        elif OpenAI is not None and openai_key:
            # Fallback to OpenAI
            self.openai_client = OpenAI(api_key=openai_key)
            self.use_gemini = False

    def is_available(self) -> bool:
        return self.use_gemini or (self.openai_client is not None)

    def transcribe_audio(self, audio_path: str) -> dict:
        """Transcribe an audio file and return text + word-level timestamps.
        
        Now uses Google Gemini for audio transcription.
        Gemini supports files up to 2GB, so no need to split!

        Returns a dict with:
          - 'text': full transcription
          - 'segments': list of dicts with 'start', 'end', 'text'
        """
        if not self.is_available():
            raise RuntimeError(
                "AI is not configured. Set GEMINI_API_KEY in your .env file."
            )

        # Transcribe with Gemini (no file size limit needed - Gemini supports up to 2GB)
        if self.use_gemini:
            return self._transcribe_with_gemini(audio_path)
        else:
            # Fallback to OpenAI if Gemini not available
            file_size = os.path.getsize(audio_path)
            max_size = 25 * 1024 * 1024  # OpenAI Whisper limit
            if file_size > max_size:
                return self._transcribe_large_audio(audio_path, file_size)
            return self._transcribe_with_openai(audio_path)

    def _transcribe_with_gemini(self, audio_path: str) -> dict:
        """Transcribe audio using Gemini API."""
        try:
            # Upload audio file to Gemini
            print(f"Uploading audio file to Gemini: {audio_path}")
            audio_file = genai.upload_file(audio_path)
            print(f"File uploaded: {audio_file.name}, State: {audio_file.state.name}")
            
            # Wait for file to be processed
            import time
            max_wait = 300  # 5 minutes max
            waited = 0
            while audio_file.state.name == "PROCESSING":
                if waited >= max_wait:
                    raise RuntimeError(f"Gemini is taking too long to process the audio (>{max_wait}s). Try a shorter video.")
                time.sleep(2)
                waited += 2
                audio_file = genai.get_file(audio_file.name)
                print(f"Still processing... ({waited}s elapsed)")
            
            if audio_file.state.name == "FAILED":
                raise RuntimeError(f"Gemini failed to process audio file: {audio_file.state}")
            
            print(f"File ready! State: {audio_file.state.name}")
            
            # Create simplified prompt for transcription
            prompt = (
                "Transcribe this audio file completely. "
                "Include all spoken words. "
                "Return ONLY the transcription text, nothing else."
            )
            
            # Generate transcription
            print("Requesting transcription from Gemini...")
            response = self.gemini_model.generate_content([prompt, audio_file])
            
            # Check if response is valid
            if not response or not response.text:
                raise RuntimeError("Gemini returned empty response for transcription")
            
            content = response.text.strip()
            print(f"Transcription received: {len(content)} characters")
            
            # Return simple format (no timestamps for now - Gemini doesn't provide them reliably)
            return {"text": content, "segments": []}
                
        except Exception as e:
            error_msg = str(e).lower()
            print(f"Gemini transcription error: {str(e)}")
            
            # Check if it's a copyright/safety issue
            if "copyright" in error_msg or "candidate" in error_msg or "safety" in error_msg:
                # Try to fall back to OpenAI Whisper if available
                if self.openai_client or os.getenv("OPENAI_API_KEY"):
                    print("Gemini detected copyrighted content. Falling back to OpenAI Whisper...")
                    try:
                        return self._transcribe_with_openai(audio_path)
                    except Exception as whisper_error:
                        raise RuntimeError(
                            f"Gemini blocked transcription (copyrighted content detected).\n"
                            f"OpenAI Whisper fallback also failed: {str(whisper_error)}\n\n"
                            f"Try using a different video without copyrighted content."
                        )
                else:
                    raise RuntimeError(
                        f"Gemini transcription failed: Copyrighted content detected.\n\n"
                        f"Gemini's safety filters blocked the transcription.\n"
                        f"To bypass this, add your OPENAI_API_KEY to .env file for Whisper fallback.\n\n"
                        f"Or use a video without copyrighted content."
                    )
            else:
                raise RuntimeError(f"Gemini transcription failed: {str(e)}")

    def _transcribe_with_openai(self, audio_path: str) -> dict:
        """Fallback: Transcribe using OpenAI Whisper."""
        if self.openai_client is None:
            openai_key = os.getenv("OPENAI_API_KEY")
            if OpenAI is None or not openai_key:
                raise RuntimeError(
                    "Neither Gemini nor OpenAI is configured for transcription."
                )
            self.openai_client = OpenAI(api_key=openai_key)

        # File is small enough - transcribe directly
        with open(audio_path, "rb") as audio_file:
            # Use whisper-1 model with verbose_json to get timestamps
            response = self.openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="verbose_json",
            )

        # response is an object with .text and .segments (if verbose_json)
        full_text = response.text or ""
        segments = []
        if hasattr(response, "segments") and response.segments:
            for seg in response.segments:
                # Access attributes directly (not .get() - they're objects, not dicts)
                segments.append({
                    "start": getattr(seg, "start", 0.0),
                    "end": getattr(seg, "end", 0.0),
                    "text": getattr(seg, "text", "").strip(),
                })

        return {"text": full_text, "segments": segments}

    def _transcribe_large_audio_gemini(self, audio_path: str, file_size: int) -> dict:
        """Split large audio into chunks and transcribe each with Gemini."""
        if VideoFileClip is None:
            raise RuntimeError("MoviePy is required to split large audio files.")

        # Calculate number of chunks needed (aim for ~20 MB per chunk)
        max_chunk_size = 20 * 1024 * 1024  # 20 MB to be safe
        num_chunks = int((file_size / max_chunk_size) + 1)

        # Load the audio to split it by time
        from moviepy.editor import AudioFileClip
        with AudioFileClip(audio_path) as audio:
            duration = audio.duration
            chunk_duration = duration / num_chunks

            all_segments = []
            full_text_parts = []

            for i in range(num_chunks):
                start_time = i * chunk_duration
                end_time = min((i + 1) * chunk_duration, duration)

                # Create temp chunk file
                chunk_path = audio_path.replace(".mp3", f"_chunk_{i}.mp3")
                try:
                    chunk_audio = audio.subclip(start_time, end_time)
                    chunk_audio.write_audiofile(
                        chunk_path,
                        bitrate="32k",
                        verbose=False,
                        logger=None
                    )

                    # Transcribe this chunk with Gemini
                    chunk_result = self._transcribe_with_gemini(chunk_path)
                    chunk_text = chunk_result.get("text", "")
                    full_text_parts.append(chunk_text)

                    # Adjust segment timestamps to account for chunk offset
                    chunk_segments = chunk_result.get("segments", [])
                    for seg in chunk_segments:
                        all_segments.append({
                            "start": seg["start"] + start_time,
                            "end": seg["end"] + start_time,
                            "text": seg["text"],
                        })

                finally:
                    # Clean up chunk file
                    if os.path.isfile(chunk_path):
                        os.remove(chunk_path)

        return {"text": " ".join(full_text_parts), "segments": all_segments}

    def _transcribe_large_audio(self, audio_path: str, file_size: int) -> dict:
        """Legacy method: Split large audio into chunks and transcribe with OpenAI Whisper."""
        if VideoFileClip is None:
            raise RuntimeError("MoviePy is required to split large audio files.")

        # Calculate number of chunks needed (aim for ~20 MB per chunk)
        max_chunk_size = 20 * 1024 * 1024  # 20 MB to be safe
        num_chunks = int((file_size / max_chunk_size) + 1)

        # Load the audio to split it by time
        from moviepy.editor import AudioFileClip
        with AudioFileClip(audio_path) as audio:
            duration = audio.duration
            chunk_duration = duration / num_chunks

            all_segments = []
            full_text_parts = []

            for i in range(num_chunks):
                start_time = i * chunk_duration
                end_time = min((i + 1) * chunk_duration, duration)

                # Create temp chunk file
                chunk_path = audio_path.replace(".mp3", f"_chunk_{i}.mp3")
                try:
                    chunk_audio = audio.subclip(start_time, end_time)
                    chunk_audio.write_audiofile(
                        chunk_path,
                        bitrate="32k",
                        verbose=False,
                        logger=None
                    )

                    # Transcribe this chunk
                    with open(chunk_path, "rb") as chunk_file:
                        response = self.openai_client.audio.transcriptions.create(
                            model="whisper-1",
                            file=chunk_file,
                            response_format="verbose_json",
                        )

                    chunk_text = response.text or ""
                    full_text_parts.append(chunk_text)

                    # Adjust segment timestamps to account for chunk offset
                    if hasattr(response, "segments") and response.segments:
                        for seg in response.segments:
                            all_segments.append({
                                "start": getattr(seg, "start", 0.0) + start_time,
                                "end": getattr(seg, "end", 0.0) + start_time,
                                "text": getattr(seg, "text", "").strip(),
                            })

                finally:
                    # Clean up chunk file
                    if os.path.isfile(chunk_path):
                        os.remove(chunk_path)

        return {"text": " ".join(full_text_parts), "segments": all_segments}

    def generate_video_metadata(self, context: str) -> dict:
        """Use an AI model to suggest title, description, and thumbnail idea.

        The `context` can be a short description of the show, transcript
        snippets, or anything that describes the video(s).
        """
        if not self.is_available():
            raise RuntimeError(
                "AI is not configured. Set GEMINI_API_KEY or OPENAI_API_KEY in your .env file."
            )

        prompt = (
            "You are a YouTube and TikTok content expert. "
            "Given information about a stand-up comedy video, you create "
            "an engaging YouTube-style title, a detailed description with "
            "good SEO keywords, and a short thumbnail text idea.\n\n"
            "Video info / transcript snippets:\n" + context.strip() + "\n\n"
            "Return ONLY a JSON object with keys: title, description, thumbnail_idea.\n"
            "Example: {\"title\": \"...\", \"description\": \"...\", \"thumbnail_idea\": \"...\"}\n\n"
            "JSON:"
        )

        if self.use_gemini:
            # Use Gemini
            response = self.gemini_model.generate_content(prompt)
            content = response.text
        else:
            # Use OpenAI
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a YouTube content expert."},
                    {"role": "user", "content": prompt},
                ],
            )
            content = response.choices[0].message.content
        try:
            data = json.loads(content)
        except Exception:
            # Fallback: try to extract a JSON object from the text
            start = content.find("{")
            end = content.rfind("}")
            if start != -1 and end != -1 and end > start:
                data = json.loads(content[start : end + 1])
            else:
                raise ValueError("AI response was not valid JSON: " + content)

        title = str(data.get("title", "")).strip()
        description = str(data.get("description", "")).strip()
        thumbnail_idea = str(data.get("thumbnail_idea", "")).strip()

        return {
            "title": title,
            "description": description,
            "thumbnail_idea": thumbnail_idea,
        }

    def generate_hashtags(self, title: str, description: str) -> list[str]:
        """Generate relevant hashtags for YouTube/TikTok based on clip content."""
        if not self.is_available():
            return ["#comedy", "#standup", "#funny"]

        prompt = (
            "You are a social media expert specializing in YouTube and TikTok. "
            "Generate 10-15 relevant, trending hashtags for a comedy clip.\n\n"
            f"Title: {title}\n"
            f"Description: {description}\n\n"
            "Generate hashtags for maximum reach on YouTube and TikTok.\n"
            "Return ONLY a JSON array of hashtags (with # symbol).\n"
            "Example: [\"#comedy\", \"#standup\", \"#funny\", ...]\n\n"
            "JSON array:"
        )

        try:
            if self.use_gemini:
                # Use Gemini
                response = self.gemini_model.generate_content(prompt)
                content = response.text
            else:
                # Use OpenAI
                response = self.openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "You are a social media expert."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.5,
                )
                content = response.choices[0].message.content

            # Try to parse as JSON array
            start = content.find("[")
            end = content.rfind("]")
            if start != -1 and end != -1:
                hashtags = json.loads(content[start : end + 1])
                if isinstance(hashtags, list):
                    return [str(h).strip() for h in hashtags if h]
        except Exception:
            pass

        # Fallback hashtags if AI fails
        return [
            "#comedy", "#standup", "#funny", "#comedian", 
            "#standupcomedy", "#humor", "#lol", "#laughs"
        ]

    def identify_story_clips(self, transcript: str, min_duration: int = 30, max_duration: int = 300) -> list[dict]:
        """Analyze a transcript and identify natural story/joke boundaries.

        Returns a list of suggested clips, each with:
          - start_time (seconds)
          - end_time (seconds)
          - title
          - description
          - thumbnail_idea
        """
        if not self.is_available():
            raise RuntimeError(
                "AI is not configured. Set GEMINI_API_KEY or OPENAI_API_KEY in your .env file."
            )

        # Validate transcript
        if not transcript or not transcript.strip():
            raise ValueError("Transcript is empty. Cannot identify clips without transcription.")

        # Check if transcript has timestamps
        if "[" not in transcript or "s" not in transcript:
            # Transcript doesn't have timestamps, add a note
            transcript_note = "(Note: Transcript without precise timestamps - estimate times based on content flow)\n" + transcript
        else:
            transcript_note = transcript

        full_prompt = (
            "You are an expert comedy video editor specializing in stand-up content for TikTok and YouTube Shorts. "
            "Your goal is to identify COMPLETE, SELF-CONTAINED jokes and stories that will perform well on social media.\n\n"
            "CRITICAL RULES:\n"
            "1. COMPLETE JOKE STRUCTURE: Each clip MUST include:\n"
            "   - Setup (context/premise)\n"
            "   - Build-up (the story/situation)\n"
            "   - Punchline (the funny payoff)\n"
            "   - Brief pause/reaction after punchline (1-3 seconds)\n\n"
            "2. NEVER cut in the middle of a joke or story\n"
            "3. Each clip must be SELF-CONTAINED (understandable without prior context)\n"
            "4. Avoid clips that reference 'earlier' or 'as I mentioned before'\n"
            "5. Look for:\n"
            "   - Strong opening hooks (attention-grabbing first line)\n"
            "   - Clear narrative arc (beginning → middle → end)\n"
            "   - Memorable punchlines or callbacks\n"
            "   - Audience reactions (laughter, applause) as natural endpoints\n\n"
            f"6. Clip length: {min_duration}-{max_duration} seconds"
            + ("\n   - FLEXIBLE: Prioritize complete joke structure over strict time limits\n"
               "   - A joke can be ANY length if it's truly complete and entertaining\n"
               "   - Short clips (15-60s) = single joke with strong punchline\n"
               "   - Medium clips (60-180s) = short story or multiple related jokes\n"
               "   - Long clips (180-600s) = complete story arc with callbacks\n"
               if max_duration >= 600 else
               "\n   - Shorter clips (30-60s) = single joke with strong punchline\n"
               "   - Medium clips (60-180s) = short story or multiple related jokes\n"
               "   - Longer clips (180-300s) = complete story arc with callbacks\n")
            + "\n"
            "7. TITLES should be:\n"
            "   - Attention-grabbing and specific (not generic)\n"
            "   - Hint at the punchline WITHOUT spoiling it\n"
            "   - Use emotional triggers (relatable, shocking, surprising)\n"
            "   - Examples: 'Airport Security Got Me Arrested' NOT 'Funny Airport Story'\n\n"
            "8. DESCRIPTIONS should:\n"
            "   - Summarize the joke/story in 1-2 sentences\n"
            "   - Include relevant keywords for discovery\n"
            "   - Tease the punchline to create curiosity\n\n"
            "9. THUMBNAIL IDEAS should:\n"
            "   - Capture the peak moment or emotion\n"
            "   - Be visually specific (facial expression, gesture, scenario)\n"
            "   - Examples: 'Shocked face with TSA agent in background' NOT 'Airport scene'\n\n"
            "10. PRIORITIZE:\n"
            "    - Relatable everyday situations\n"
            "    - Universal experiences (travel, relationships, work, family)\n"
            "    - Strong punchlines with clear audience reactions\n"
            "    - Moments that make you go 'wait, WHAT happened?!'\n\n"
            "===== TASK =====\n"
            "Analyze this stand-up comedy transcript and identify 5-10 of the BEST clips for viral social media content.\n\n"
            "TRANSCRIPT WITH TIMESTAMPS:\n"
            "```\n"
            + transcript_note.strip() + "\n"
            "```\n\n"
            "Focus on complete jokes with clear setups and punchlines. "
            "Each clip should be shareable, relatable, and entertaining on its own.\n\n"
            "IMPORTANT: Return ONLY a valid JSON array, nothing else. Format:\n"
            '[{"start_time": 10.5, "end_time": 65.2, "title": "When TSA Found My Bomb", '
            '"description": "Hilarious misunderstanding at airport security.", '
            '"thumbnail_idea": "Shocked comedian with hands up"}]\n\n'
            "JSON array:"
        )

        if self.use_gemini:
            # Use Gemini
            response = self.gemini_model.generate_content(full_prompt)
            content = response.text
        else:
            # Use OpenAI (gpt-4o)
            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are an expert comedy video editor."},
                    {"role": "user", "content": full_prompt},
                ],
                temperature=0.7,
            )
            content = response.choices[0].message.content
        try:
            clips = json.loads(content)
        except Exception:
            # Try to extract JSON array from the text
            start = content.find("[")
            end = content.rfind("]")
            if start != -1 and end != -1 and end > start:
                clips = json.loads(content[start : end + 1])
            else:
                raise ValueError("AI response was not a valid JSON array: " + content)

        if not isinstance(clips, list):
            raise ValueError("AI did not return a list of clips.")

        # Validate and clean
        validated = []
        for clip in clips:
            if not isinstance(clip, dict):
                continue
            start_time = float(clip.get("start_time", 0))
            end_time = float(clip.get("end_time", 0))
            if end_time <= start_time:
                continue
            # Ensure clip is within acceptable duration range
            duration = end_time - start_time
            if duration < min_duration * 0.5:  # Allow 50% shorter than min
                continue
            if duration > max_duration * 1.5:  # Allow 50% longer than max
                continue
            validated.append({
                "start_time": start_time,
                "end_time": end_time,
                "title": str(clip.get("title", "")).strip(),
                "description": str(clip.get("description", "")).strip(),
                "thumbnail_idea": str(clip.get("thumbnail_idea", "")).strip(),
            })

        return validated


class ThumbnailGenerator:
    """Generate YouTube-style thumbnails for video clips."""
    
    @staticmethod
    def create_thumbnail(
        video_path: str,
        output_path: str,
        title: str,
        thumbnail_idea: str = ""
    ) -> bool:
        """Create a YouTube thumbnail from a video frame with text overlay.
        
        Args:
            video_path: Path to the video file
            output_path: Where to save the thumbnail (e.g., 'clip_1_thumbnail.jpg')
            title: Title text to overlay on thumbnail
            thumbnail_idea: AI-generated idea for what to capture (optional)
        
        Returns:
            True if successful, False otherwise
        """
        if not PIL_AVAILABLE:
            print("Pillow (PIL) not installed. Skipping thumbnail generation.")
            return False
        
        if VideoFileClip is None:
            print("MoviePy not available. Skipping thumbnail generation.")
            return False
        
        try:
            # Extract a frame from the middle of the video
            with VideoFileClip(video_path) as video:
                # Get frame from 1/3 into the video (usually has good facial expression)
                timestamp = video.duration / 3
                frame = video.get_frame(timestamp)
            
            # Convert frame to PIL Image
            img = Image.fromarray(frame)
            
            # Resize to YouTube thumbnail size (1280x720)
            img = img.resize((1280, 720), Image.Resampling.LANCZOS)
            
            # Create a semi-transparent overlay for text
            overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            
            # Draw semi-transparent black bar at bottom for text
            bar_height = 180
            draw.rectangle(
                [(0, 720 - bar_height), (1280, 720)],
                fill=(0, 0, 0, 200)  # Black with 200/255 opacity
            )
            
            # Try to load a bold font, fallback to default if not available
            try:
                # Try common font paths (works on Windows, Mac, Linux)
                font_size = 60
                font_paths = [
                    "C:/Windows/Fonts/arialbd.ttf",  # Windows
                    "/System/Library/Fonts/Helvetica.ttc",  # macOS
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux
                ]
                font = None
                for font_path in font_paths:
                    if os.path.exists(font_path):
                        font = ImageFont.truetype(font_path, font_size)
                        break
                if font is None:
                    font = ImageFont.load_default()
            except Exception:
                font = ImageFont.load_default()
            
            # Prepare text - split into lines if too long
            max_chars = 40
            if len(title) > max_chars:
                words = title.split()
                lines = []
                current_line = []
                for word in words:
                    current_line.append(word)
                    if len(' '.join(current_line)) > max_chars:
                        current_line.pop()
                        lines.append(' '.join(current_line))
                        current_line = [word]
                if current_line:
                    lines.append(' '.join(current_line))
                title_text = '\n'.join(lines[:3])  # Max 3 lines
            else:
                title_text = title
            
            # Draw text with outline for better readability
            text_y = 720 - bar_height + 30
            text_x = 40
            
            # Draw outline (black)
            for offset_x in [-2, -1, 0, 1, 2]:
                for offset_y in [-2, -1, 0, 1, 2]:
                    draw.text(
                        (text_x + offset_x, text_y + offset_y),
                        title_text,
                        font=font,
                        fill=(0, 0, 0, 255)
                    )
            
            # Draw main text (white/yellow)
            draw.text(
                (text_x, text_y),
                title_text,
                font=font,
                fill=(255, 255, 100, 255)  # Yellowish white
            )
            
            # Convert back to RGB and composite
            img = img.convert('RGBA')
            img = Image.alpha_composite(img, overlay)
            img = img.convert('RGB')
            
            # Save as JPEG
            img.save(output_path, 'JPEG', quality=95)
            print(f"Thumbnail saved: {output_path}")
            return True
            
        except Exception as e:
            print(f"Error creating thumbnail: {str(e)}")
            return False
    
    @staticmethod
    def create_ai_thumbnail(
        output_path: str,
        title: str,
        description: str,
        thumbnail_idea: str = ""
    ) -> bool:
        """Create a YouTube thumbnail using Gemini AI image generation.
        
        Args:
            output_path: Where to save the thumbnail (e.g., 'clip_1_thumbnail.jpg')
            title: Title of the clip
            description: Description of the clip
            thumbnail_idea: AI-generated idea for thumbnail
        
        Returns:
            True if successful, False otherwise
        """
        if genai is None:
            print("Google Generative AI not available. Skipping AI thumbnail generation.")
            return False
        
        try:
            # Create detailed prompt for thumbnail generation
            prompt = (
                f"Create a professional YouTube thumbnail for a stand-up comedy video. "
                f"The thumbnail should be eye-catching, colorful, and engaging.\n\n"
                f"Video Title: {title}\n"
            )
            
            if thumbnail_idea:
                prompt += f"Thumbnail Concept: {thumbnail_idea}\n"
            
            if description:
                prompt += f"Context: {description}\n\n"
            
            prompt += (
                "\nStyle requirements:\n"
                "- Bold, vibrant colors (yellows, oranges, reds)\n"
                "- High contrast and attention-grabbing\n"
                "- YouTube thumbnail aesthetic (1280x720)\n"
                "- Include large, bold text with the title\n"
                "- Comedy/entertainment theme\n"
                "- Professional and clickable design\n"
                "- Expressive facial expressions or reactions\n"
                "- Clean, uncluttered composition"
            )
            
            print(f"Generating AI thumbnail with prompt: {prompt[:200]}...")
            
            # Generate image using Imagen via Gemini API
            model = genai.GenerativeModel('gemini-2.0-flash-exp')
            
            # Note: Gemini doesn't directly generate images yet.
            # We'll use a workaround: request an image URL or use Imagen API
            # For now, create a placeholder with PIL and AI-suggested colors
            
            # Get AI suggestions for thumbnail design
            design_prompt = (
                f"Based on this comedy video title '{title}' and description '{description}', "
                "suggest a thumbnail design with: 1) Background color (hex), 2) Text color (hex), "
                "3) Emoji/icon to use, 4) Short catchy text (5-10 words max). "
                "Return as JSON: {\"bg_color\": \"#...\", \"text_color\": \"#...\", \"emoji\": \"...\", \"text\": \"...\"}"
            )
            
            response = model.generate_content(design_prompt)
            content = response.text.strip()
            
            # Parse JSON response
            import json
            start = content.find("{")
            end = content.rfind("}")
            if start != -1 and end != -1:
                design = json.loads(content[start:end+1])
            else:
                # Fallback design
                design = {
                    "bg_color": "#FF6B35",
                    "text_color": "#FFFFFF",
                    "emoji": "😂",
                    "text": title[:50]
                }
            
            # Create thumbnail using PIL with AI-suggested design
            if not PIL_AVAILABLE:
                print("Pillow not available for AI thumbnail rendering.")
                return False
            
            # Create base image
            img = Image.new('RGB', (1280, 720), color=design.get('bg_color', '#FF6B35'))
            draw = ImageDraw.Draw(img)
            
            # Add gradient effect
            for i in range(720):
                alpha = int(255 * (1 - i / 720))
                draw.rectangle([(0, i), (1280, i+1)], fill=(0, 0, 0, alpha // 3))
            
            # Load font
            try:
                font_size = 80
                font_paths = [
                    "C:/Windows/Fonts/impact.ttf",
                    "C:/Windows/Fonts/arialbd.ttf",
                    "/System/Library/Fonts/Helvetica.ttc",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                ]
                font = None
                for font_path in font_paths:
                    if os.path.exists(font_path):
                        font = ImageFont.truetype(font_path, font_size)
                        break
                if font is None:
                    font = ImageFont.load_default()
            except Exception:
                font = ImageFont.load_default()
            
            # Draw emoji at top
            emoji = design.get('emoji', '😂')
            try:
                emoji_font = ImageFont.truetype("C:/Windows/Fonts/seguiemj.ttf", 120)
                draw.text((1280//2, 100), emoji, font=emoji_font, fill='white', anchor='mm')
            except:
                pass  # Skip emoji if font not available
            
            # Draw text with outline
            text = design.get('text', title)[:80]
            text_color = design.get('text_color', '#FFFFFF')
            
            # Word wrap
            words = text.split()
            lines = []
            current_line = []
            max_width = 1200
            
            for word in words:
                test_line = ' '.join(current_line + [word])
                bbox = draw.textbbox((0, 0), test_line, font=font)
                if bbox[2] - bbox[0] > max_width:
                    if current_line:
                        lines.append(' '.join(current_line))
                        current_line = [word]
                else:
                    current_line.append(word)
            if current_line:
                lines.append(' '.join(current_line))
            
            # Draw text lines
            y_offset = 350
            for line in lines[:3]:
                # Draw outline
                for offset_x in [-3, -2, -1, 0, 1, 2, 3]:
                    for offset_y in [-3, -2, -1, 0, 1, 2, 3]:
                        draw.text(
                            (1280//2 + offset_x, y_offset + offset_y),
                            line,
                            font=font,
                            fill='black',
                            anchor='mm'
                        )
                # Draw main text
                draw.text(
                    (1280//2, y_offset),
                    line,
                    font=font,
                    fill=text_color,
                    anchor='mm'
                )
                y_offset += 90
            
            # Save
            img.save(output_path, 'JPEG', quality=95)
            print(f"AI-generated thumbnail saved: {output_path}")
            return True
            
        except Exception as e:
            print(f"Error creating AI thumbnail: {str(e)}")
            return False


class ClipsApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("YouTube & TikTok Clips Manager")
        self.root.geometry("900x650")

        # State variables
        self.input_video = StringVar()
        self.output_dir = StringVar()
        self.intro_video = StringVar()
        self.outro_video = StringVar()
        self.logo_image = StringVar()
        self.logo_position = StringVar(value="bottom-right")

        # Clip length in seconds (radio buttons)
        # 0 = Auto mode (AI decides best length for complete jokes)
        self.clip_length = IntVar(value=0)  # default to Auto mode

        self.use_intro = BooleanVar(value=False)
        self.use_outro = BooleanVar(value=False)
        self.use_logo = BooleanVar(value=False)
        self.generate_thumbnails = BooleanVar(value=True)  # Generate thumbnails by default
        self.thumbnail_method = StringVar(value="video_frame")  # "video_frame" or "ai_generated"
        self.add_subtitles = BooleanVar(value=False)  # Subtitles off by default

        self.ai_helper = AIHelper()

        self._build_ui()

    # ---------------- GUI helpers -----------------
    def _build_ui(self) -> None:
        if ttk is None:
            # Fallback to basic widgets if ttk is not available
            self._build_basic_ui()
        else:
            self._build_ttk_ui()

    def _build_basic_ui(self) -> None:
        # Layout is very similar but uses base tkinter widgets only
        Label(self.root, text="Source video:").grid(row=0, column=0, sticky="w", padx=10, pady=5)
        Entry(self.root, textvariable=self.input_video, width=60).grid(row=0, column=1, padx=5, pady=5)
        Button(self.root, text="Browse", command=self.browse_input).grid(row=0, column=2, padx=5, pady=5)

        Label(self.root, text="Output folder:").grid(row=1, column=0, sticky="w", padx=10, pady=5)
        Entry(self.root, textvariable=self.output_dir, width=60).grid(row=1, column=1, padx=5, pady=5)
        Button(self.root, text="Browse", command=self.browse_output).grid(row=1, column=2, padx=5, pady=5)

        # Clip length options
        Label(self.root, text="Clip length:").grid(row=2, column=0, sticky="nw", padx=10, pady=5)
        clip_frame = Text(self.root, height=1, width=1)  # dummy for layout
        clip_frame.grid_forget()
        self._build_clip_length_radios_basic(start_row=2)

        # Intro/outro/logo
        row = 6
        Button(self.root, text="Intro video (optional)", command=self.browse_intro).grid(row=row, column=0, sticky="w", padx=10, pady=5)
        Entry(self.root, textvariable=self.intro_video, width=60).grid(row=row, column=1, padx=5, pady=5)
        row += 1

        Button(self.root, text="Outro video (optional)", command=self.browse_outro).grid(row=row, column=0, sticky="w", padx=10, pady=5)
        Entry(self.root, textvariable=self.outro_video, width=60).grid(row=row, column=1, padx=5, pady=5)
        row += 1

        Button(self.root, text="Logo image (optional)", command=self.browse_logo).grid(row=row, column=0, sticky="w", padx=10, pady=5)
        Entry(self.root, textvariable=self.logo_image, width=60).grid(row=row, column=1, padx=5, pady=5)
        row += 1

        Button(self.root, text="Generate clips (fixed length)", command=self.on_generate_clips).grid(row=row, column=0, padx=10, pady=10)
        Button(self.root, text="Generate Smart Clips (AI)", command=self.on_generate_smart_clips).grid(row=row, column=1, padx=10, pady=10)

        # AI section
        row += 1
        Label(self.root, text="Video context for AI (optional):").grid(row=row, column=0, sticky="nw", padx=10, pady=5)
        self.ai_input = Text(self.root, height=5, width=60)
        self.ai_input.grid(row=row, column=1, columnspan=2, padx=5, pady=5, sticky="nsew")
        row += 1

        Button(self.root, text="Suggest title/description/thumbnail", command=self.on_generate_metadata).grid(
            row=row, column=0, padx=10, pady=10
        )

        self.ai_output = Text(self.root, height=8, width=60, state=DISABLED)
        self.ai_output.grid(row=row, column=1, columnspan=2, padx=5, pady=5, sticky="nsew")

    def _build_clip_length_radios_basic(self, start_row: int) -> None:
        # Simple radio buttons without ttk
        options = [
            ("Auto (AI decides best length)", 0),
            ("10 seconds", 10),
            ("30 seconds", 30),
            ("< 1 minute (45s)", 45),
            ("1-5 minutes (3 min)", 180),
            ("5-10 minutes (7 min)", 420),
            ("> 10 minutes (12 min)", 720),
        ]
        row = start_row
        col = 1
        for label, value in options:
            Button(self.root, text=label, command=lambda v=value: self.clip_length.set(v)).grid(
                row=row, column=col, sticky="w", padx=5, pady=2
            )
            row += 1

    def _build_ttk_ui(self) -> None:
        # Main layout using ttk
        padding = {"padx": 8, "pady": 4}

        # Source and output
        ttk.Label(self.root, text="Source video:").grid(row=0, column=0, sticky="w", **padding)
        ttk.Entry(self.root, textvariable=self.input_video, width=70).grid(row=0, column=1, **padding)
        ttk.Button(self.root, text="Browse", command=self.browse_input).grid(row=0, column=2, **padding)

        ttk.Label(self.root, text="Output folder:").grid(row=1, column=0, sticky="w", **padding)
        ttk.Entry(self.root, textvariable=self.output_dir, width=70).grid(row=1, column=1, **padding)
        ttk.Button(self.root, text="Browse", command=self.browse_output).grid(row=1, column=2, **padding)

        # Clip length section
        ttk.Label(self.root, text="Clip length presets:").grid(row=2, column=0, sticky="nw", **padding)
        clip_frame = ttk.Frame(self.root)
        clip_frame.grid(row=2, column=1, columnspan=2, sticky="w", **padding)

        options = [
            ("Auto (AI decides best length)", 0),
            ("10 seconds", 10),
            ("30 seconds", 30),
            ("< 1 minute (45s)", 45),
            ("1-5 minutes (3 min)", 180),
            ("5-10 minutes (7 min)", 420),
            ("> 10 minutes (12 min)", 720),
        ]

        for idx, (label, value) in enumerate(options):
            ttk.Radiobutton(
                clip_frame,
                text=label,
                variable=self.clip_length,
                value=value,
            ).grid(row=idx // 2, column=idx % 2, sticky="w", padx=4, pady=2)

        # Intro / outro / logo section
        row = 4
        ttk.Checkbutton(
            self.root,
            text="Use intro video",
            variable=self.use_intro,
        ).grid(row=row, column=0, sticky="w", **padding)
        ttk.Entry(self.root, textvariable=self.intro_video, width=70).grid(row=row, column=1, **padding)
        ttk.Button(self.root, text="Browse", command=self.browse_intro).grid(row=row, column=2, **padding)
        row += 1

        ttk.Checkbutton(
            self.root,
            text="Use outro video",
            variable=self.use_outro,
        ).grid(row=row, column=0, sticky="w", **padding)
        ttk.Entry(self.root, textvariable=self.outro_video, width=70).grid(row=row, column=1, **padding)
        ttk.Button(self.root, text="Browse", command=self.browse_outro).grid(row=row, column=2, **padding)
        row += 1

        ttk.Checkbutton(
            self.root,
            text="Use logo image",
            variable=self.use_logo,
        ).grid(row=row, column=0, sticky="w", **padding)
        ttk.Entry(self.root, textvariable=self.logo_image, width=70).grid(row=row, column=1, **padding)
        ttk.Button(self.root, text="Browse", command=self.browse_logo).grid(row=row, column=2, **padding)
        row += 1

        ttk.Label(self.root, text="Logo position:").grid(row=row, column=0, sticky="w", **padding)
        if ttk is not None:
            combo = ttk.Combobox(
                self.root,
                textvariable=self.logo_position,
                values=["top-left", "top-right", "bottom-left", "bottom-right"],
                state="readonly",
            )
            combo.grid(row=row, column=1, sticky="w", **padding)
        row += 1

        # Thumbnail generation option
        ttk.Checkbutton(
            self.root,
            text="Generate YouTube thumbnails",
            variable=self.generate_thumbnails,
        ).grid(row=row, column=0, sticky="w", **padding)
        
        # Thumbnail method dropdown
        ttk.Label(self.root, text="Thumbnail method:").grid(row=row, column=1, sticky="w", **padding)
        if ttk is not None:
            thumbnail_combo = ttk.Combobox(
                self.root,
                textvariable=self.thumbnail_method,
                values=["video_frame", "ai_generated"],
                state="readonly",
                width=15
            )
            thumbnail_combo.grid(row=row, column=2, sticky="w", **padding)
        row += 1

        # Subtitle option
        ttk.Checkbutton(
            self.root,
            text="Add subtitles/captions to video",
            variable=self.add_subtitles,
        ).grid(row=row, column=0, columnspan=2, sticky="w", **padding)
        row += 1

        ttk.Button(self.root, text="Generate clips (fixed length)", command=self.on_generate_clips).grid(
            row=row, column=0, **padding
        )
        ttk.Button(self.root, text="Generate Smart Clips (AI)", command=self.on_generate_smart_clips).grid(
            row=row, column=1, sticky="w", **padding
        )

        # AI section
        row += 1
        ttk.Label(self.root, text="Video context for AI (optional):").grid(
            row=row, column=0, sticky="nw", **padding
        )
        self.ai_input = Text(self.root, height=6, width=70)
        self.ai_input.grid(row=row, column=1, columnspan=2, sticky="nsew", **padding)
        row += 1

        ttk.Button(
            self.root,
            text="Suggest title / description / thumbnail",
            command=self.on_generate_metadata,
        ).grid(row=row, column=0, **padding)

        self.ai_output = Text(self.root, height=10, width=70, state=DISABLED)
        self.ai_output.grid(row=row, column=1, columnspan=2, sticky="nsew", **padding)

        # Make output area expandable
        self.root.grid_rowconfigure(row, weight=1)
        self.root.grid_columnconfigure(1, weight=1)

    # ------------- File dialog callbacks -------------
    def browse_input(self) -> None:
        path = filedialog.askopenfilename(
            title="Select source video",
            filetypes=[("Video files", "*.mp4;*.mov;*.mkv;*.avi;*.flv"), ("All files", "*.*")],
        )
        if path:
            self.input_video.set(path)

    def browse_output(self) -> None:
        path = filedialog.askdirectory(title="Select output folder")
        if path:
            self.output_dir.set(path)

    def browse_intro(self) -> None:
        path = filedialog.askopenfilename(
            title="Select intro video",
            filetypes=[("Video files", "*.mp4;*.mov;*.mkv;*.avi;*.flv"), ("All files", "*.*")],
        )
        if path:
            self.intro_video.set(path)
            self.use_intro.set(True)

    def browse_outro(self) -> None:
        path = filedialog.askopenfilename(
            title="Select outro video",
            filetypes=[("Video files", "*.mp4;*.mov;*.mkv;*.avi;*.flv"), ("All files", "*.*")],
        )
        if path:
            self.outro_video.set(path)
            self.use_outro.set(True)

    def browse_logo(self) -> None:
        path = filedialog.askopenfilename(
            title="Select logo image",
            filetypes=[("Image files", "*.png;*.jpg;*.jpeg"), ("All files", "*.*")],
        )
        if path:
            self.logo_image.set(path)
            self.use_logo.set(True)

    # ------------- Actions -------------
    def on_generate_clips(self) -> None:
        input_path = self.input_video.get().strip()
        output_dir = self.output_dir.get().strip()
        if not input_path:
            messagebox.showerror("Missing input", "Please select a source video.")
            return
        if not output_dir:
            messagebox.showerror("Missing output", "Please select an output folder.")
            return

        clip_length = int(self.clip_length.get() or 0)
        if clip_length <= 0:
            messagebox.showerror("Invalid clip length", "Please select a valid clip length.")
            return

        intro = self.intro_video.get().strip() if self.use_intro.get() else None
        outro = self.outro_video.get().strip() if self.use_outro.get() else None
        logo = self.logo_image.get().strip() if self.use_logo.get() else None
        logo_pos = self.logo_position.get().strip() or "bottom-right"

        try:
            clips = VideoProcessor.split_video(
                input_path=input_path,
                output_dir=output_dir,
                clip_length_seconds=clip_length,
                intro_path=intro or None,
                outro_path=outro or None,
                logo_path=logo or None,
                logo_position=logo_pos,
                output_prefix="clip",
            )
        except Exception as exc:
            messagebox.showerror("Error while generating clips", str(exc))
            return

        messagebox.showinfo(
            "Done",
            f"Created {len(clips)} clip(s) in:\n{output_dir}",
        )

    def on_generate_smart_clips(self) -> None:
        """AI-powered clip generation: transcribe, identify stories, then cut."""
        input_path = self.input_video.get().strip()
        output_dir = self.output_dir.get().strip()
        if not input_path:
            messagebox.showerror("Missing input", "Please select a source video.")
            return
        if not output_dir:
            messagebox.showerror("Missing output", "Please select an output folder.")
            return

        if not self.ai_helper.is_available():
            messagebox.showerror(
                "AI not configured",
                "AI is not configured. To use Smart Clips:\n"
                "1) Install the OpenAI Python package: pip install openai\n"
                "2) Set the OPENAI_API_KEY environment variable to your API key."
            )
            return

        # Step 1: Extract audio
        audio_path = os.path.join(output_dir, "temp_audio.mp3")
        try:
            messagebox.showinfo(
                "Processing",
                "Step 1/3: Extracting audio from video...\nThis may take a moment."
            )
            VideoProcessor.extract_audio(input_path, audio_path)
        except Exception as exc:
            messagebox.showerror("Audio extraction failed", str(exc))
            return

        # Step 2: Transcribe with timestamps
        try:
            messagebox.showinfo(
                "Processing",
                "Step 2/3: Transcribing audio with Gemini...\nThis may take several minutes for long videos."
            )
            transcription = self.ai_helper.transcribe_audio(audio_path)
            full_text = transcription.get("text", "")
            segments = transcription.get("segments", [])
            
            # Validate transcription result
            if not full_text or not full_text.strip():
                raise RuntimeError(
                    "Transcription returned empty text. Possible issues:\n"
                    "1. Audio file has no speech\n"
                    "2. Gemini API error\n"
                    "3. Audio quality too poor\n\n"
                    "Try with a different video or check your Gemini API key."
                )
                
        except Exception as exc:
            messagebox.showerror("Transcription failed", str(exc))
            # Clean up temp audio before returning
            if os.path.isfile(audio_path):
                os.remove(audio_path)
            return
        finally:
            # Clean up temp audio
            if os.path.isfile(audio_path):
                os.remove(audio_path)

        # Format transcript with timestamps for AI
        formatted_transcript = ""
        if segments:
            # We have timestamped segments
            for seg in segments:
                start = seg.get("start", 0.0)
                end = seg.get("end", 0.0)
                text = seg.get("text", "").strip()
                if text:
                    formatted_transcript += f"[{start:.1f}s - {end:.1f}s] {text}\n"
        else:
            # No segments, use full text with estimated timestamps
            # Split by sentences and estimate timing
            sentences = full_text.split(". ")
            duration_per_sentence = 5.0  # Rough estimate
            current_time = 0.0
            for sentence in sentences:
                if sentence.strip():
                    sentence = sentence.strip() + "."
                    end_time = current_time + duration_per_sentence
                    formatted_transcript += f"[{current_time:.1f}s - {end_time:.1f}s] {sentence}\n"
                    current_time = end_time
        
        # Final check
        if not formatted_transcript.strip():
            messagebox.showerror(
                "Transcription Error",
                "Could not format transcript properly. The transcription text was:\n\n" +
                full_text[:200] + "...\n\n" +
                "Please try again or use a different video."
            )
            return

        # Step 3: Ask AI to identify story clips
        try:
            messagebox.showinfo(
                "Processing",
                "Step 3/3: Identifying complete stories/jokes with AI...\nAlmost done!"
            )
            # Use clip length presets as min/max hints
            clip_length = int(self.clip_length.get() or 0)
            
            if clip_length == 0:
                # Auto mode - let AI decide best length with no constraints
                min_dur = 15  # Minimum for any joke to make sense
                max_dur = 600  # Maximum 10 minutes for single clip
                messagebox.showinfo(
                    "Auto Mode",
                    "AI will find complete jokes with NO time constraints.\n"
                    "Clips can be anywhere from 15 seconds to 10 minutes,\n"
                    "based purely on joke structure and completeness."
                )
            else:
                # Use selected preset as a hint
                min_dur = max(10, clip_length - 30)
                max_dur = clip_length + 60

            clip_specs = self.ai_helper.identify_story_clips(
                formatted_transcript,
                min_duration=min_dur,
                max_duration=max_dur,
            )

            if not clip_specs:
                messagebox.showwarning(
                    "No clips found",
                    "AI could not identify any suitable clips from the transcript.\n"
                    "Try a different video or use fixed-length mode."
                )
                return

        except Exception as exc:
            messagebox.showerror("AI analysis failed", str(exc))
            return

        # Step 4: Create the clips
        intro = self.intro_video.get().strip() if self.use_intro.get() else None
        outro = self.outro_video.get().strip() if self.use_outro.get() else None
        logo = self.logo_image.get().strip() if self.use_logo.get() else None
        logo_pos = self.logo_position.get().strip() or "bottom-right"

        try:
            created_clips = VideoProcessor.create_smart_clips(
                input_path=input_path,
                output_dir=output_dir,
                clip_specs=clip_specs,
                intro_path=intro or None,
                outro_path=outro or None,
                logo_path=logo or None,
                logo_position=logo_pos,
            )
        except Exception as exc:
            messagebox.showerror("Error while creating clips", str(exc))
            return

        # Add subtitles if enabled
        if self.add_subtitles.get() and segments:
            messagebox.showinfo(
                "Processing",
                "Adding subtitles to video clips...\nThis may take a few minutes."
            )
            for clip in created_clips:
                video_path = clip["path"]
                start_time = clip["start_time"]
                end_time = clip["end_time"]
                
                # Filter segments that fall within this clip's timeframe
                clip_segments = []
                for seg in segments:
                    seg_start = seg.get("start", 0.0)
                    seg_end = seg.get("end", 0.0)
                    
                    # Check if segment overlaps with clip
                    if seg_end > start_time and seg_start < end_time:
                        # Adjust timestamps relative to clip start
                        adjusted_seg = {
                            "start": max(0, seg_start - start_time),
                            "end": min(end_time - start_time, seg_end - start_time),
                            "text": seg.get("text", "")
                        }
                        clip_segments.append(adjusted_seg)
                
                # If we have segments, add subtitles
                if clip_segments:
                    temp_path = video_path.replace(".mp4", "_temp.mp4")
                    try:
                        # Rename original to temp
                        import shutil
                        shutil.move(video_path, temp_path)
                        
                        # Add subtitles (temp -> final)
                        VideoProcessor.add_subtitles_to_video(
                            temp_path,
                            video_path,
                            clip_segments
                        )
                        
                        # Remove temp file
                        if os.path.isfile(temp_path):
                            os.remove(temp_path)
                    except Exception as e:
                        print(f"Error adding subtitles to {video_path}: {str(e)}")
                        # Restore original if subtitle failed
                        if os.path.isfile(temp_path):
                            shutil.move(temp_path, video_path)

        # Generate hashtags and create .txt files for each clip
        messagebox.showinfo(
            "Processing",
            "Generating hashtags and creating description files..."
        )

        for clip in created_clips:
            # Generate hashtags for this clip
            hashtags = self.ai_helper.generate_hashtags(
                clip["title"],
                clip["description"]
            )

            # Create .txt file with same name as video
            video_path = clip["path"]
            txt_path = video_path.replace(".mp4", ".txt")

            txt_content = f"""TITLE:
{clip['title']}

DESCRIPTION:
{clip['description']}

THUMBNAIL IDEA:
{clip['thumbnail_idea']}

HASHTAGS:
{' '.join(hashtags)}

---
YOUTUBE UPLOAD TEXT (copy everything below):
---

{clip['title']}

{clip['description']}

{' '.join(hashtags)}
"""

            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(txt_content)

            # Add hashtags to clip metadata
            clip["hashtags"] = hashtags
            
            # Generate thumbnail if enabled
            if self.generate_thumbnails.get():
                thumbnail_path = video_path.replace(".mp4", "_thumbnail.jpg")
                
                # Choose thumbnail generation method
                method = self.thumbnail_method.get()
                if method == "ai_generated":
                    # Use AI-generated thumbnail
                    ThumbnailGenerator.create_ai_thumbnail(
                        thumbnail_path,
                        clip["title"],
                        clip["description"],
                        clip.get("thumbnail_idea", "")
                    )
                else:
                    # Use video frame thumbnail (default)
                    ThumbnailGenerator.create_thumbnail(
                        video_path,
                        thumbnail_path,
                        clip["title"],
                        clip.get("thumbnail_idea", "")
                    )
                
                clip["thumbnail_path"] = thumbnail_path

        # Show results with metadata
        summary = f"Created {len(created_clips)} smart clip(s) in:\n{output_dir}\n\n"
        summary += "Clips:\n"
        for clip in created_clips[:5]:  # show first 5
            summary += f"- {clip['title']}\n"
        if len(created_clips) > 5:
            summary += f"... and {len(created_clips) - 5} more.\n"
        
        # Build feature list
        features = []
        features.append(".txt file with metadata")
        if self.generate_thumbnails.get():
            features.append("YouTube thumbnail")
        if self.add_subtitles.get():
            features.append("burned-in subtitles")
        
        summary += f"\n✅ Each clip has: {', '.join(features)}!"

        messagebox.showinfo("Smart Clips Done!", summary)

        # Optionally write metadata to a JSON file
        metadata_path = os.path.join(output_dir, "clips_metadata.json")
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(created_clips, f, indent=2, ensure_ascii=False)

        messagebox.showinfo(
            "Metadata saved",
            f"Clip metadata (titles, descriptions, etc.) saved to:\n{metadata_path}"
        )

    def on_generate_metadata(self) -> None:
        context = self.ai_input.get("1.0", END).strip()
        if not context:
            if not messagebox.askyesno(
                "No context",
                "You did not enter any video context or transcript. "
                "Do you want to let the AI guess based on a generic stand-up comedy show?",
            ):
                return
            context = "A stand-up comedy show with multiple jokes and crowd interactions."

        if not self.ai_helper.is_available():
            messagebox.showerror(
                "AI not configured",
                "AI is not configured. To use this feature:\n"
                "1) Install the OpenAI Python package: pip install openai\n"
                "2) Set the OPENAI_API_KEY environment variable to your API key."
            )
            return

        try:
            metadata = self.ai_helper.generate_video_metadata(context)
        except Exception as exc:
            messagebox.showerror("AI error", str(exc))
            return

        output_text = (
            "Suggested title:\n" + metadata.get("title", "") + "\n\n"
            "Suggested description:\n" + metadata.get("description", "") + "\n\n"
            "Thumbnail idea:\n" + metadata.get("thumbnail_idea", "")
        )

        self.ai_output.configure(state=NORMAL)
        self.ai_output.delete("1.0", END)
        self.ai_output.insert("1.0", output_text)
        self.ai_output.configure(state=DISABLED)


def main() -> None:
    root = Tk()
    app = ClipsApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
