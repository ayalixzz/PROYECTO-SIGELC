import { useState } from 'react'
import type { FormEvent } from 'react'
import { Aviso, Icon, Marca } from '../components/Ui'
import { api, ErrorApi, mensaje } from '../services/api'
import type { Sesion } from '../services/api'

export function Login({ onLogin }: { onLogin: (value: Sesion) => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [visible, setVisible] = useState(false)
  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    setBusy(true)
    setError('')
    try {
      const response = await api<Sesion>('sesion/', {
        method: 'POST',
        body: { username: String(data.get('username')).trim(), password: data.get('password') },
      })
      onLogin(response)
    } catch (err) {
      setError(err instanceof ErrorApi && [400, 401, 403].includes(err.status)
        ? 'No se pudo iniciar sesión. Verifica tus credenciales y que tu cuenta esté habilitada para la demo.'
        : mensaje(err))
    } finally { setBusy(false) }
  }
  return <main className="login">
    <section className="login-story" aria-label="SIGELC">
      <Marca />
      <div className="login-story-copy">
        <p className="eyebrow">Gestión pública</p>
        <h1>Seguimiento contractual en un solo lugar.</h1>
        <p>Consulta expedientes, revisa vencimientos y prepara requerimientos desde tu espacio de trabajo.</p>
        <div className="login-features">
          <span><Icon name="folder" /> Expedientes</span>
          <span><Icon name="clock" /> Seguimiento</span>
          <span><Icon name="file" /> Documentos</span>
        </div>
      </div>
      <p className="login-environment"><span className="environment-dot" /> Demostración local con datos ficticios</p>
    </section>
    <section className="login-access" aria-labelledby="login-heading">
      <div className="login-form-wrap">
        <div className="login-mobile-brand"><Marca small /></div>
        <div className="login-form-icon"><Icon name="logo" size={26} /></div>
        <h2 id="login-heading">Iniciar sesión</h2>
        <p className="subtle">Ingresa con tu cuenta de abogado.</p>
        {error && <Aviso tipo="error">{error}</Aviso>}
        <form className="login-form" onSubmit={login}>
          <label className="field"><span>Usuario</span>
            <input name="username" autoComplete="username" placeholder="Nombre de usuario" required maxLength={150} />
          </label>
          <label className="field"><span>Contraseña</span>
            <span className="password-field">
              <input name="password" type={visible ? 'text' : 'password'} autoComplete="current-password" placeholder="Tu contraseña" required />
              <button className="icon-button" type="button" onClick={() => setVisible(!visible)}
                aria-label={visible ? 'Ocultar contraseña' : 'Mostrar contraseña'}><Icon name={visible ? 'eyeOff' : 'eye'} size={20} /></button>
            </span>
          </label>
          <button type="submit" className="button button-primary login-submit" disabled={busy}>
            {busy ? 'Ingresando…' : 'Ingresar a SIGELC'}<Icon name="arrow" size={18} />
          </button>
        </form>
      </div>
    </section>
  </main>
}
