// All records are synthetic. "current" models the audited data-loss points;
// it is not a capture from a running Sakura instance.
const detail = (label, value) => ({ label, value: String(value) });
const SETTINGS = '相关设置或数据操作没有完成。';
const GENERIC = '这项功能没有按预期工作，Sakura 仍在运行。';
const REPLY = '这次回复没有正常完成。';
const failure = (id, message, code, raw, extra = {}) => ({ id, message, code, raw, ...extra });
export const scenarios = {
  import: [
    failure('char-missing', '导入角色包失败', 'CHARACTER_IMPORT_FAILED', "FileNotFoundError: [Errno 2] No such file or directory: '<角色目录>/portrait/default.png'", {
      command:'characters.settings.import', stage:'校验角色资源', elapsed:2063, description:SETTINGS, duplicate:true,
      chain:"CharacterSettingsError: 角色包导入失败\n→ CharacterArchiveError: 角色资源不存在\n→ FileNotFoundError: portrait/default.png 不存在", site:'app.config.character_archive:import_character_archive:118', system:'errno=2',
    }),
    failure('char-zip', '导入角色包失败', 'CHARACTER_IMPORT_FAILED', 'BadZipFile: File is not a zip file', {
      command:'characters.settings.import', stage:'打开角色包', elapsed:34, description:SETTINGS, duplicate:true,
      chain:'CharacterSettingsError: 角色包导入失败\n→ CharacterArchiveError: 不是有效的 Sakura .char ZIP 包\n→ BadZipFile: File is not a zip file', site:'zipfile:__init__:1404',
    }),
    failure('char-permission', '导入角色包失败', 'CHARACTER_IMPORT_FAILED', "PermissionError: [WinError 5] Access is denied: '<角色目录>/sakura/manifest.json'", {
      command:'characters.settings.import', stage:'写入角色清单', elapsed:817, description:SETTINGS, duplicate:true,
      chain:'CharacterSettingsError: 角色包导入失败\n→ PermissionError: Access is denied', site:'app.config.character_archive:import_character_archive:121', system:'winerror=5 · errno=13',
    }),
  ],
  api: [
    failure('api-auth','模型回复请求失败','MODEL_REQUEST_FAILED','AuthenticationError: Incorrect API key provided: [REDACTED].',{
      category:'API', event:'api.request.failed', stage:'模型服务请求', description:REPLY, elapsed:421, http:401,
      currentDiagnostic:'Incorrect API key provided: [REDACTED].', providerCode:'invalid_api_key', site:'app.llm.api_client:request:752',
    }),
    failure('api-rate','模型回复请求失败','MODEL_REQUEST_FAILED','RateLimitError: Rate limit reached for requests. Limit: 60/min. Please try again in 12s.',{
      category:'API', event:'api.request.failed', stage:'模型服务请求', description:REPLY, elapsed:108, http:429,
      currentDiagnostic:'Rate limit reached for requests.', providerCode:'rate_limit_exceeded', site:'app.llm.api_client:request:752',
    }),
    failure('api-timeout','测试模型服务失败','PROVIDER_REQUEST_FAILED','ReadTimeout: The read operation timed out after 30.0 seconds.',{
      command:'providers.settings.test', category:'CORE', stage:'等待响应', description:SETTINGS, elapsed:30002,
      chain:'ProviderRequestError: 模型服务请求失败\n→ ReadTimeout: The read operation timed out', site:'httpx._transports.default:handle_request:249',
    }),
  ],
  tts: [
    failure('tts-spawn','GPT-SoVITS 服务启动失败','TTS_SERVICE_UNAVAILABLE',"FileNotFoundError: [WinError 2] The system cannot find the file specified: '<TTS 目录>/runtime/python.exe'",{
      scope:'tts', category:'TTS', event:'tts.service.failed', stage:'启动服务进程', description:'语音服务未能启动，文字回复仍可使用。', elapsed:86,
      chain:'RuntimeError: TTS 服务启动失败\n→ FileNotFoundError: runtime/python.exe 不存在', site:'subprocess:_execute_child:1548', system:'winerror=2',
    }),
    failure('tts-weights','角色语音模型加载失败','TTS_WEIGHTS_LOAD_FAILED','RuntimeError: Error(s) in loading state_dict for SynthesizerTrn:\nsize mismatch for enc_p.emb.weight: checkpoint shape [322, 192], current model shape [400, 192].',{
      scope:'tts', category:'TTS', event:'tts.weights.failed', stage:'加载 SoVITS 权重', description:'SoVITS 角色语音权重加载失败，文字回复仍可使用。', elapsed:1894,
      currentDiagnostic:'Error(s) in loading state_dict for SynthesizerTrn: size mismatch', site:'torch.nn.modules.module:load_state_dict:2584',
    }),
    failure('tts-disk','语音录制保存失败','AUDIO_RECORDING_INVALID',"OSError: [Errno 28] No space left on device: '<语音记录目录>/audio-003.wav'",{
      scope:'tts', category:'TTS', event:'tts.recording.failed', stage:'保存合成音频', description:'语音记录未能保存。', elapsed:12,
      chain:'AudioInputError: AUDIO_RECORDING_INVALID\n→ OSError: No space left on device', site:'app.core_host.tts_boundary:_accept_audio:763', system:'errno=28',
    }),
  ],
  plugins: [
    failure('plugin-dependency','插件启动失败','PLUGIN_LIFECYCLE_FAILED',"ModuleNotFoundError: No module named 'soundfile'",{
      scope:'plugins', category:'PLUGIN', source:'plugin', pluginId:'demo.voice', pluginName:'语音扩展', event:'runtime.message', stage:'导入插件依赖', description:'相关工具没有正常完成。', elapsed:104,
      chain:"PluginLifecycleError: 插件启动失败\n→ ModuleNotFoundError: No module named 'soundfile'", site:'demo.voice:activate:24',
    }),
    failure('plugin-rollback','本地插件安装失败','PLUGIN_INSTALL_ROLLBACK_FAILED',"PermissionError: [WinError 32] The process cannot access the file because it is being used by another process: '<插件目录>/demo.capture/plugin.py'",{
      scope:'plugins', category:'PLUGIN', source:'plugin', pluginId:'demo.capture', pluginName:'截图扩展', event:'runtime.message', stage:'替换插件文件', description:'相关工具没有正常完成。', elapsed:626,
      chain:'PluginInstallError: 安装插件失败\n→ PermissionError: 目标文件正在被另一个进程使用', site:'app.plugins.installer:install:180', system:'winerror=32',
      recovery:"PermissionError: [WinError 5] Access is denied: '<插件备份目录>/demo.capture'", recoverySite:'app.plugins.installer:rollback:214',
    }),
  ],
  mixed: [
    failure('config-save','保存工具设置失败','CONFIG_SAVE_FAILED',"PermissionError: [Errno 13] Permission denied: '<用户目录>/config/tool_settings.yaml'",{
      description:SETTINGS, command:'tools.settings.save', stage:'写入配置文件', elapsed:9, site:'app.core_host.tool_settings:save:119', system:'errno=13',
    }),
    failure('ui-js','界面运行异常','UNHANDLED_ERROR',"TypeError: Cannot read properties of undefined (reading 'displayName')",{
      source:'webview', category:'UI', event:'webview.error.unhandled', description:'界面发生异常，部分操作可能无法完成。', stage:'渲染角色列表', severity:'error',
      site:'desktop/frontend/settings/characters.js:renderCharacter:184',
    }),
    failure('long-response','模型回复请求失败','MODEL_REQUEST_FAILED','BadRequestError: Invalid request: messages[12].tool_calls[0].function.arguments is not valid JSON.\nExpected a comma or closing brace after the property value at line 1, column 243.\nThe upstream gateway rejected the request before model execution. Request ID: req-demo-8372.\nParameter: messages[12].tool_calls[0].function.arguments; type: invalid_request_error; code: invalid_tool_arguments.\nGateway: https://api.example.test/v1/chat/completions; Authorization: [REDACTED].',{
      category:'API', event:'api.request.failed', stage:'模型服务请求', description:REPLY, elapsed:302, http:400, site:'app.llm.api_client:request:752', providerCode:'invalid_tool_arguments',
    }),
    failure('unknown','后台请求失败','REQUEST_FAILED','未记录底层原因',{
      description:GENERIC, command:'demo.unknown', stage:'请求处理', elapsed:17, missing:true,
    }),
  ],
};

export function createSnapshot(mode, scenario) {
  const examples = scenarios[scenario] || scenarios.import;
  const scope = examples[0].scope || 'software';
  let sequence = 0;
  const records = [];
  const push = value => records.push({ sequence:++sequence, timestamp:`15:42:${String(sequence + 1).padStart(2,'0')}`, scopes:[scope], severity:'info', category:'APP', eventCode:'shell.started', message:'Sakura 已启动', details:[], source:'rust', ...value });
  if (scope === 'software') push({ details:[detail('当前版本','0.0.0-demo')] });
  for (const [index, example] of examples.entries()) {
    const timestamp = `15:42:${String(3 + index * 10).padStart(2,'0')}`;
    const isProposed = mode === 'proposed';
    const fields = [detail('错误代码',example.code)];
    if (isProposed) {
      fields.unshift(detail('原始报错',example.raw));
      fields.push(detail('失败阶段',example.stage));
      if (example.chain) fields.push(detail('异常链',example.chain));
      if (example.site) fields.push(detail('代码位置',example.site));
      if (example.system) fields.push(detail('系统错误',example.system));
      if (example.recovery) fields.push(detail('回滚报错',example.recovery), detail('回滚位置',example.recoverySite));
    } else if (example.currentDiagnostic) fields.unshift(detail('诊断',example.currentDiagnostic));
    if (example.http) fields.push(detail('HTTP 状态',example.http));
    if (example.providerCode) fields.push(detail('服务错误码',example.providerCode));
    if (example.command) fields.push(detail('请求',example.command));
    if (example.elapsed !== undefined) fields.push(detail('耗时',`${example.elapsed} ms`));
    if (isProposed) fields.push(detail('应用版本','0.0.0-demo'));
    push({ timestamp, scopes:[example.scope || 'software'], severity:example.severity || 'warning', category:example.category || 'CORE', eventCode:example.event || 'ipc.request.failed', message:example.message,
      source:example.source || 'rust', description:example.description, details:fields, correlationId:example.id.slice(0,8),
      ...(example.pluginId ? {pluginId:example.pluginId,pluginName:example.pluginName} : {}),
    });
    if (!isProposed && example.duplicate) push({ timestamp, severity:'warning', category:'UI', eventCode:'webview.command.failed', source:'webview', message:'界面命令失败', description:GENERIC, details:[detail('错误代码','INVOKE_FAILED'),detail('耗时',`${example.elapsed} ms`)] });
  }
  return { schemaVersion:3, runId:`demo-${mode}-${scenario}`, latestSequence:sequence, resetRequired:true, records, failedFiles:scenario === 'mixed' ? ['runtime'] : [] };
}
