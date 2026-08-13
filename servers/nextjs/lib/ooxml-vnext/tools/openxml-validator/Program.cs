using System.Reflection;
using System.Security.Cryptography;
using System.Text.Json;
using DocumentFormat.OpenXml;
using DocumentFormat.OpenXml.Packaging;
using DocumentFormat.OpenXml.Validation;

const int MaximumReportedErrors = 500;

static string Sha256(string path)
{
    using FileStream stream = new(path, FileMode.Open, FileAccess.Read, FileShare.Read);
    return Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
}

static void Emit(object value)
{
    Console.WriteLine(JsonSerializer.Serialize(value, new JsonSerializerOptions
    {
        WriteIndented = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
    }));
}

if (args.Length != 1)
{
    Emit(new
    {
        schemaVersion = "ooxml-vnext/openxml-sdk-validator/v1",
        status = "usage-error",
        message = "Usage: OpenXmlValidator <input.pptx|input.pptm>",
    });
    return 1;
}

string inputPath = Path.GetFullPath(args[0]);
string extension = Path.GetExtension(inputPath);
if (!File.Exists(inputPath) ||
    !(extension.Equals(".pptx", StringComparison.OrdinalIgnoreCase) ||
      extension.Equals(".pptm", StringComparison.OrdinalIgnoreCase)))
{
    Emit(new
    {
        schemaVersion = "ooxml-vnext/openxml-sdk-validator/v1",
        status = "input-error",
        input = Path.GetFileName(inputPath),
        message = "Input must be an existing .pptx or .pptm file.",
    });
    return 1;
}

string beforeDigest = Sha256(inputPath);
try
{
    using PresentationDocument document = PresentationDocument.Open(inputPath, false);
    OpenXmlValidator validator = new(FileFormatVersions.Microsoft365);
    List<object> errors = [];
    int errorCount = 0;
    foreach (ValidationErrorInfo error in validator.Validate(document))
    {
        errorCount += 1;
        if (errors.Count < MaximumReportedErrors)
        {
            errors.Add(new
            {
                id = error.Id,
                error.ErrorType,
                error.Description,
                part = error.Part?.Uri.ToString(),
                path = error.Path?.XPath,
                node = error.Node?.LocalName,
            });
        }
    }

    string afterDigest = Sha256(inputPath);
    bool unchanged = StringComparer.Ordinal.Equals(beforeDigest, afterDigest);
    string sdkVersion = typeof(OpenXmlElement).Assembly
        .GetCustomAttribute<AssemblyInformationalVersionAttribute>()?
        .InformationalVersion ?? typeof(OpenXmlElement).Assembly.GetName().Version?.ToString() ?? "unknown";
    bool passed = errorCount == 0 && unchanged;
    Emit(new
    {
        schemaVersion = "ooxml-vnext/openxml-sdk-validator/v1",
        status = passed ? "passed" : "failed",
        validator = new
        {
            implementation = "Microsoft Open XML SDK",
            package = "DocumentFormat.OpenXml",
            packageVersionPin = "3.5.1",
            loadedAssemblyVersion = sdkVersion,
            target = "Microsoft365",
        },
        input = new
        {
            fileName = Path.GetFileName(inputPath),
            byteLength = new FileInfo(inputPath).Length,
            sha256Before = beforeDigest,
            sha256After = afterDigest,
            unchanged,
            openedReadOnly = true,
        },
        validation = new
        {
            errorCount,
            reportedErrorCount = errors.Count,
            truncated = errorCount > errors.Count,
            errors,
        },
        limitations = new[]
        {
            "Schema validation is not a PowerPoint reopen or rendering test.",
            "A zero-error result does not establish that PowerPoint displayed no repair dialog.",
        },
    });
    return passed ? 0 : 2;
}
catch (Exception exception)
{
    string afterDigest = Sha256(inputPath);
    Emit(new
    {
        schemaVersion = "ooxml-vnext/openxml-sdk-validator/v1",
        status = "failed",
        validator = new
        {
            implementation = "Microsoft Open XML SDK",
            package = "DocumentFormat.OpenXml",
            packageVersionPin = "3.5.1",
            target = "Microsoft365",
        },
        input = new
        {
            fileName = Path.GetFileName(inputPath),
            sha256Before = beforeDigest,
            sha256After = afterDigest,
            unchanged = StringComparer.Ordinal.Equals(beforeDigest, afterDigest),
            openedReadOnly = true,
        },
        error = new
        {
            type = exception.GetType().FullName,
            exception.Message,
        },
    });
    return 1;
}
