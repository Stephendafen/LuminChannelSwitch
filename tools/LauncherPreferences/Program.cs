using System.Reflection;
using System.Security.Cryptography;
using System.Text.Json.Nodes;

// Keep the publisher's settings format in its signed assembly. The helper returns
// proposed config bytes on stdout; the switcher backs up and commits the change.
if (args.Length != 3) return 2;
try
{
    var assemblyFile = Path.GetFullPath(args[0]);
    using (var source = File.OpenRead(assemblyFile))
    {
        var hash = Convert.ToHexString(SHA256.HashData(source));
        if (!hash.Equals("95825e6639e166f16e9b9dc9c07f3620e29f0adfee57033be125442543ce6c73", StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("Launcher assembly mismatch");
    }
    var assembly = Assembly.LoadFrom(assemblyFile);
    var codec = assembly.GetType("Lumin.Launcher.Utilities.AesEncryption", true)!;
    var decode = codec.GetMethod("Decrypt", new[] { typeof(byte[]) })!
        .CreateDelegate<Func<byte[], string>>();
    var encode = codec.GetMethod("Encrypt", new[] { typeof(string) })!
        .CreateDelegate<Func<string, byte[]>>();
    var prefsDir = Path.GetFullPath(args[1]);
    var gamePath = Path.GetFullPath(args[2]);
    var cacheFile = Path.Combine(prefsDir, "cache.lmc");
    var configFile = Path.Combine(prefsDir, "config.lmc");
    JsonObject read(string file) => JsonNode.Parse(decode(File.ReadAllBytes(file)))!.AsObject();
    if (File.Exists(cacheFile))
    {
        var state = read(cacheFile)["Task.State"]?.ToString();
        if (!String.IsNullOrEmpty(state) && state != "0") return 3;
    }
    var obj = File.Exists(configFile) ? read(configFile) : new JsonObject
    {
        ["language"] = "zh-cn", ["auto_start"] = false, ["close_action"] = 1,
        ["speed_limit"] = 0, ["extract_while_downloading"] = true, ["max_pending_extractions"] = 2,
    };
    if (String.Equals(obj["game_path"]?.ToString(), gamePath, StringComparison.OrdinalIgnoreCase))
        return 0;
    obj["game_path"] = gamePath;
    var encrypted = encode(obj.ToJsonString());
    if (!JsonNode.DeepEquals(obj, JsonNode.Parse(decode(encrypted))))
        throw new InvalidDataException("Settings round trip failed");
    Console.Write(Convert.ToBase64String(encrypted));
    return 0;
}
catch (Exception e)
{
    Console.Error.Write(e.GetType().Name);
    return 1;
}
