import alibabacloud_oss_v2 as oss

def main():
    """
    Python SDK V2 客户端初始化配置说明：

    1. 签名版本：Python SDK V2 默认使用 V4 签名，提供更高的安全性
    2. Region配置：初始化 Client 时，必须指定阿里云 Region ID 作为请求地域标识
    3. Endpoint配置：
       - 可通过Endpoint参数自定义服务请求的访问域名
       - 当不指定 Endpoint 时，将根据 Region 自动构造公网访问域名
    4. 协议配置：
       - SDK 默认使用 HTTPS 协议构造访问域名
       - 如需使用 HTTP 协议，在指定域名时明确指定
    """

    # 从环境变量中加载凭证信息，用于身份验证
    credentials_provider = oss.credentials.EnvironmentVariableCredentialsProvider()

    # 加载SDK的默认配置，并设置凭证提供者
    cfg = oss.config.load_default()
    cfg.credentials_provider = credentials_provider

    # 方式一：只填写Region（推荐）
    # 必须指定Region ID，SDK会根据Region自动构造HTTPS访问域名
    # cfg.region = '<region-id>' 

    # # 方式二：同时填写Region和Endpoint
    # # 必须指定Region ID
    cfg.region = 'oss-cn-beijing'
    # # 填写Bucket所在地域对应的外网Endpoint
    cfg.endpoint = 'education-test-private.oss-cn-beijing.aliyuncs.com'

    # 使用配置好的信息创建OSS客户端
    client = oss.Client(cfg)

    # 定义要上传的字符串内容
    text_string = "Hello, OSS!"
    data = text_string.encode('utf-8')  # 将字符串编码为UTF-8字节串

    # 执行上传对象的请求，指定存储空间名称、对象名称和数据内容
    result = client.put_object(oss.PutObjectRequest(
        bucket="education-test-private",
        key="example.txt",
        body=data,
    ))

    # 输出请求的结果状态码、请求ID、ETag，用于检查请求是否成功
    print(f'status code: {result.status_code}\n'
          f'request id: {result.request_id}\n'
          f'etag: {result.etag}'
    )

# 当此脚本被直接运行时，调用main函数
if __name__ == "__main__":
    main()  # 脚本入口，当文件被直接运行时调用main函数