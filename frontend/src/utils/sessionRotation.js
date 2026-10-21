/**
 * 会话令牌轮换闸门。
 *
 * 改密码会让服务端把该账号此前签发的全部令牌一次性作废（会话版本号自增），
 * 然后在同一个响应里返回一对新令牌。也就是说，从"服务端把版本号 +1"到
 * "浏览器把新令牌写进 localStorage"之间存在一个窗口，这个窗口里其他在飞
 * 请求如果带着旧令牌回来，会收到 401。
 *
 * 这些 401 **不是**会话失效，但它们会走 axios 拦截器的刷新分支：拿旧 refresh
 * 去换新令牌 → 服务端比对版本号发现已作废 → 刷新失败 → 拦截器判定会话死亡
 * 并强制登出。结果是"我刚改完密码，自己却被踢下线"，这正是本次需求最不能
 * 出现的副作用。
 *
 * 所以改密码期间在这里挂一个 Promise 作闸门：拦截器遇到 401 先等闸门放行，
 * 再拿已经换好的新令牌重放原请求。放在独立模块而不是 auth store 里，是为了
 * 避免 utils/api.js 与 stores/auth.js 之间再多一层循环引用。
 */

let rotation = null

/** 开始令牌轮换，返回一个 resolve 函数用于结束闸门。 */
export function beginSessionRotation() {
  let resolve
  const promise = new Promise((res) => { resolve = res })
  rotation = promise
  return () => {
    rotation = null
    resolve()
  }
}

/** 当前是否有令牌轮换在进行中（有则返回 Promise）。 */
export function pendingSessionRotation() {
  return rotation
}

/** 重置闸门（登出、测试隔离时用）。 */
export function resetSessionRotation() {
  rotation = null
}
