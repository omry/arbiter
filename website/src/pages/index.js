import clsx from 'clsx';
import Heading from '@theme/Heading';
import Layout from '@theme/Layout';
import Link from '@docusaurus/Link';
import styles from './index.module.css';

const features = [
  {
    title: 'Credentials stay hidden',
    body:
      'Agents can use sensitive service accounts without seeing the underlying credentials.',
  },
  {
    title: 'Policies define access',
    body:
      'Arbiter operators configure precise policies for what agents can do with each account.',
  },
  {
    title: 'Capabilities are discoverable',
    body:
      'Agents get a structured capability API instead of ad hoc service access.',
  },
];

const featuredPlugins = [
  {
    name: 'SMTP',
    body: 'Send mail through configured accounts and policy gates.',
    to: '/docs/plugins/smtp',
  },
  {
    name: 'IMAP',
    body: 'Read and manage mailboxes through scoped operations.',
    to: '/docs/plugins/imap',
  },
];

function HomepageHeader() {
  return (
    <header className={clsx('hero hero--primary', styles.heroBanner)}>
      <div className={clsx('container', styles.heroInner)}>
        <div className={styles.heroCopy}>
          <Heading as="h1" className="hero__title">
            Arbiter
          </Heading>
          <p className="hero__subtitle">
            A capability firewall between AI agents and services.
          </p>
          <div className={styles.buttons}>
            <Link
              className="button button--secondary button--lg"
              to="/docs/">
              Read the docs
            </Link>
          </div>
        </div>
        <aside className={styles.heroSide} aria-label="Featured plugins">
          <img
            className={styles.heroLogo}
            src="img/logo.svg"
            alt="Arbiter logo"
          />
          <div className={styles.pluginStack}>
            <div className={styles.pluginEyebrow}>Featured plugins</div>
            {featuredPlugins.map((plugin) => (
              <Link className={styles.pluginLink} to={plugin.to} key={plugin.name}>
                <span className={styles.pluginName}>{plugin.name}</span>
                <span className={styles.pluginBody}>{plugin.body}</span>
              </Link>
            ))}
          </div>
        </aside>
      </div>
    </header>
  );
}

export default function Home() {
  return (
    <Layout
      title="Arbiter"
      description="A capability firewall between AI agents and services">
      <HomepageHeader />
      <main>
        <section className="padding-vert--xl">
          <div className="container aa-feature-grid">
            {features.map((feature) => (
              <article className="aa-feature" key={feature.title}>
                <Heading as="h3">{feature.title}</Heading>
                <p>{feature.body}</p>
              </article>
            ))}
          </div>
        </section>
      </main>
    </Layout>
  );
}
