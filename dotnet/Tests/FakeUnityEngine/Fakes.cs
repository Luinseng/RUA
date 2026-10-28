namespace UnityEngine
{
    public static class Application
    {
        public static string dataPath => "C:/Game/Game_Data";
        public static string streamingAssetsPath => "C:/Game/Game_Data/StreamingAssets";
    }
}

namespace UnityEngine.Networking
{
    public sealed class UnityWebRequest
    {
        public string Url { get; private set; }

        public static UnityWebRequest Get(string uri) => new UnityWebRequest { Url = uri };

        public static UnityWebRequest Post(string uri, string postData) => new UnityWebRequest { Url = uri + "|" + postData };
    }
}
