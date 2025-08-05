import asyncio
from typing import List
import nltk

from models.document_chunk import DocumentChunk

try:
    nltk.data.find("tokenizers/punkt", paths=["./nltk"])
except LookupError:
    nltk.download("punkt", download_dir="./nltk")


class ScoreBasedChunker:

    def extract_sentences(self, text: str, min_sentences: int) -> List[str]:
        sentences = self.extract_sentences_markdown(text)
        if len(sentences) < min_sentences:
            sentences = self.extract_sentences_nltk(text)
        if len(sentences) < min_sentences:
            sentences = self.extract_sentences_by_stop_words(text)
        if len(sentences) < min_sentences:
            sentences = self.extract_sentences_by_new_line(text)
        if len(sentences) < min_sentences:
            raise ValueError(
                f"Only {len(sentences)} sentences found, requested {min_sentences}"
            )
        return sentences

    def extract_sentences_markdown(self, text: str) -> List[str]:
        lines = text.split("\n")
        sentences = []

        for line in lines:
            line = line.strip()
            if line:
                if line.startswith("#"):
                    sentences.append(line)
                else:
                    if line.endswith((".", "!", "?")):
                        sentences.append(line)
                    else:
                        sentences.append(line)

        return sentences

    def extract_sentences_nltk(self, text: str) -> List[str]:
        sentences = nltk.sent_tokenize(text)
        return sentences

    def extract_sentences_by_stop_words(self, text: str) -> List[str]:
        sentences = []
        current_sentence = ""

        for char in text:
            current_sentence += char
            if char in ".!?":
                sentences.append(current_sentence.strip())
                current_sentence = ""

        if current_sentence.strip():
            sentences.append(current_sentence.strip())

        return [s for s in sentences if s]

    def extract_sentences_by_new_line(self, text: str) -> List[str]:
        sentences = text.split("\n")
        result = []
        for i, sentence in enumerate(sentences):
            if i < len(sentences) - 1:
                result.append(sentence + "\n")
            else:
                result.append(sentence)
        return result

    def score_sentences_for_heading(self, sentences: List[str]) -> List[float]:
        sentences_scores = []

        last_heading_index = -1
        first_heading_found = False

        for i, sentence in enumerate(sentences):
            score = 0.0

            if sentence.strip().startswith("#"):
                heading_level = len(sentence) - len(sentence.lstrip("#"))

                if heading_level <= 3:
                    score += 10.0 - (heading_level - 1) * 2.0
                else:
                    score += 4.0 - (heading_level - 4) * 0.5

                if not first_heading_found:
                    score += 5.0
                    first_heading_found = True

                if last_heading_index != -1:
                    distance = i - last_heading_index
                    distance_bonus = min(5.0, distance * 0.5)
                    score += distance_bonus

                last_heading_index = i

            sentences_scores.append(score)

        return sentences_scores

    def get_chunks(
        self, sentences: List[str], sentences_scores: List[float], top_k: int = 10
    ) -> List[DocumentChunk]:
        if not sentences_scores:
            sentences_scores = self.score_sentences_for_heading(sentences)

        chunks = []
        heading_scores = []

        for i, score in enumerate(sentences_scores):
            if score > 0:
                heading_scores.append((i, score))

        if len(heading_scores) == 0:
            return chunks

        heading_scores.sort(key=lambda x: (-x[1], x[0]))

        if len(heading_scores) <= top_k:
            selected_headings = [idx for idx, _ in heading_scores]
            selected_headings.sort()
        else:
            score_groups = {}
            for idx, score in heading_scores:
                rounded_score = round(score)
                if rounded_score not in score_groups:
                    score_groups[rounded_score] = []
                score_groups[rounded_score].append(idx)

            sorted_groups = sorted(
                score_groups.items(), key=lambda x: x[0], reverse=True
            )

            selected_headings = []

            for score, headings in sorted_groups:
                headings.sort()
                remaining_needed = top_k - len(selected_headings)

                if remaining_needed <= 0:
                    break

                if len(headings) <= remaining_needed:
                    selected_headings.extend(headings)
                else:
                    if remaining_needed == 1:
                        mid_idx = len(headings) // 2
                        selected_headings.append(headings[mid_idx])
                    elif remaining_needed == 2:
                        selected_headings.append(headings[0])
                        selected_headings.append(headings[-1])
                    else:
                        step = (len(headings) - 1) / (remaining_needed - 1)

                        for i in range(remaining_needed):
                            index = int(round(i * step))
                            if index < len(headings):
                                selected_headings.append(headings[index])

            selected_headings.sort()

        for i, heading_idx in enumerate(selected_headings):
            heading = sentences[heading_idx]

            if i + 1 < len(selected_headings):
                next_heading_idx = selected_headings[i + 1]
                content_end = next_heading_idx
            else:
                content_end = len(sentences)

            content_sentences = sentences[heading_idx + 1 : content_end]
            content = " ".join(content_sentences).strip()

            chunk = DocumentChunk(
                heading=heading,
                content=content,
                heading_index=heading_idx,
                score=sentences_scores[heading_idx],
            )
            chunks.append(chunk)
        return chunks

    async def get_n_chunks(self, text: str, n: int) -> List[DocumentChunk]:
        sentences = await asyncio.to_thread(self.extract_sentences, text, n)
        sentences_scores = await asyncio.to_thread(
            self.score_sentences_for_heading, sentences
        )
        chunks = await asyncio.to_thread(
            self.get_chunks, sentences, sentences_scores, n
        )
        if len(chunks) < n:
            raise ValueError(f"Only {len(chunks)} chunks found, requested {n}")
        return chunks
