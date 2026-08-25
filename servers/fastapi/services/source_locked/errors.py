from services.source_locked.contracts import AnalysisStage, SourceLockedErrorV1


class SourceLockedError(Exception):
    def __init__(self, code: str, stage: AnalysisStage, message: str):
        super().__init__(message)
        self.detail = SourceLockedErrorV1(code=code, stage=stage, message=message)
