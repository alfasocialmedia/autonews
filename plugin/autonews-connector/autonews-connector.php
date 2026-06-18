<?php
/**
 * Plugin Name: AutoNews Connector
 * Plugin URI:  https://autonews.app
 * Description: Conecta AutoNews con tu WordPress para publicar noticias con categorías, etiquetas, imagen destacada y SEO (Yoast / RankMath) sin necesidad de Application Password.
 * Version:     1.0.0
 * Requires at least: 5.6
 * Requires PHP: 7.4
 * Author:      AutoNews
 * License:     GPL v2 or later
 * Text Domain: autonews-connector
 */

defined( 'ABSPATH' ) || exit;

define( 'AUTONEWS_VERSION',    '1.0.0' );
define( 'AUTONEWS_OPT_TOKEN',  'autonews_api_token' );
define( 'AUTONEWS_NAMESPACE',  'autonews/v1' );

// ── Activación ────────────────────────────────────────────────────────────────

register_activation_hook( __FILE__, 'autonews_activate' );
function autonews_activate(): void {
    if ( ! get_option( AUTONEWS_OPT_TOKEN ) ) {
        update_option( AUTONEWS_OPT_TOKEN, wp_generate_password( 48, false ) );
    }
}

// ── Menú de administración ────────────────────────────────────────────────────

add_action( 'admin_menu', 'autonews_admin_menu' );
function autonews_admin_menu(): void {
    add_options_page(
        'AutoNews Connector',
        'AutoNews',
        'manage_options',
        'autonews-connector',
        'autonews_settings_page'
    );
}

function autonews_settings_page(): void {
    if ( ! current_user_can( 'manage_options' ) ) {
        return;
    }

    // Regenerar token
    if ( isset( $_POST['autonews_regenerate'] ) && check_admin_referer( 'autonews_regen' ) ) {
        update_option( AUTONEWS_OPT_TOKEN, wp_generate_password( 48, false ) );
        echo '<div class="notice notice-success is-dismissible"><p><strong>Token regenerado.</strong> Actualizá AutoNews con el nuevo token.</p></div>';
    }

    $token    = get_option( AUTONEWS_OPT_TOKEN, '' );
    $site_url = get_site_url();
    $conn     = json_encode(
        [ 'site_url' => $site_url, 'plugin_api_key' => $token ],
        JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES
    );
    ?>
    <div class="wrap">
        <h1>🔗 AutoNews Connector <span style="font-size:.6em;font-weight:400;color:#888">v<?php echo AUTONEWS_VERSION; ?></span></h1>
        <p>Copiá los datos de conexión y pegarlos en tu panel de AutoNews → <strong>Configuración → WordPress → Token del plugin</strong>.</p>

        <div class="card" style="max-width:680px;padding:24px;margin-top:20px">

            <h2 style="margin-top:0;font-size:1.1em">📋 Datos de conexión</h2>
            <textarea id="an-conn" rows="5" class="large-text code" readonly
                style="background:#f6f7f7;font-size:.85em"><?php echo esc_textarea( $conn ); ?></textarea>
            <p>
                <button type="button" class="button button-primary" onclick="anCopy('an-conn','¡Datos copiados! Pegá en AutoNews → WordPress.')">
                    📋 Copiar datos de conexión
                </button>
            </p>

            <hr>
            <h2 style="font-size:1.1em">🔑 Token individual</h2>
            <input type="text" id="an-tok" value="<?php echo esc_attr( $token ); ?>"
                class="regular-text" readonly onclick="this.select()"
                style="font-family:monospace;font-size:.85em">
            <button type="button" class="button" onclick="anCopy('an-tok','Token copiado.')">Copiar</button>

            <hr>
            <h2 style="font-size:1.1em">✅ Verificar conexión</h2>
            <p>
                <a href="<?php echo esc_url( rest_url( AUTONEWS_NAMESPACE . '/status' ) . '?token=' . urlencode( $token ) ); ?>"
                   target="_blank" class="button">Abrir endpoint de estado</a>
                <span class="description" style="margin-left:8px">Debería responder <code>{"ok":true,...}</code></span>
            </p>

            <hr>
            <h2 style="font-size:1.1em;color:#b32d2e">⚠️ Regenerar token</h2>
            <p class="description">Usá esto <strong>solo</strong> si el token fue comprometido. Deberás actualizar AutoNews inmediatamente.</p>
            <form method="post">
                <?php wp_nonce_field( 'autonews_regen' ); ?>
                <button type="submit" name="autonews_regenerate" value="1" class="button"
                    onclick="return confirm('¿Regenerar el token? Deberás actualizarlo en AutoNews.')">
                    🔄 Regenerar token
                </button>
            </form>
        </div>

        <script>
        function anCopy(id, msg) {
            var el = document.getElementById(id);
            el.select();
            if (navigator.clipboard) {
                navigator.clipboard.writeText(el.value).then(function(){ alert(msg); });
            } else {
                document.execCommand('copy');
                alert(msg);
            }
        }
        </script>
    </div>
    <?php
}

// ── REST API ──────────────────────────────────────────────────────────────────

add_action( 'rest_api_init', 'autonews_register_routes' );
function autonews_register_routes(): void {
    register_rest_route( AUTONEWS_NAMESPACE, '/status', [
        'methods'             => WP_REST_Server::READABLE,
        'callback'            => 'autonews_status',
        'permission_callback' => 'autonews_verify_token',
    ] );

    register_rest_route( AUTONEWS_NAMESPACE, '/publish', [
        'methods'             => WP_REST_Server::CREATABLE,
        'callback'            => 'autonews_publish',
        'permission_callback' => 'autonews_verify_token',
    ] );
}

function autonews_verify_token( WP_REST_Request $req ): bool {
    $stored = get_option( AUTONEWS_OPT_TOKEN, '' );
    if ( ! $stored ) {
        return false;
    }
    // Bearer token en header
    $auth = $req->get_header( 'authorization' ) ?? '';
    if ( str_starts_with( $auth, 'Bearer ' ) ) {
        return hash_equals( $stored, trim( substr( $auth, 7 ) ) );
    }
    // Fallback: ?token= en query string (útil para verificación desde navegador)
    $qt = (string) ( $req->get_param( 'token' ) ?? '' );
    return $qt && hash_equals( $stored, $qt );
}

function autonews_status(): WP_REST_Response {
    global $wp_version;
    return new WP_REST_Response( [
        'ok'             => true,
        'site_url'       => get_site_url(),
        'site_name'      => get_bloginfo( 'name' ),
        'plugin_version' => AUTONEWS_VERSION,
        'wp_version'     => $wp_version,
        'yoast'          => defined( 'WPSEO_VERSION' ),
        'rankmath'       => defined( 'RANK_MATH_VERSION' ),
    ] );
}

// ── Publicación ───────────────────────────────────────────────────────────────

function autonews_publish( WP_REST_Request $req ): WP_REST_Response {
    $p = $req->get_json_params();
    if ( ! $p ) {
        return new WP_REST_Response( [ 'success' => false, 'error' => 'Body JSON vacío o inválido' ], 400 );
    }

    $title   = sanitize_text_field( $p['title']   ?? '' );
    $content = wp_kses_post( $p['content']         ?? '' );
    $excerpt = sanitize_textarea_field( $p['excerpt'] ?? '' );
    $status  = in_array( $p['status'] ?? '', [ 'publish', 'draft', 'pending', 'private' ], true )
               ? $p['status'] : 'draft';

    if ( ! $title ) {
        return new WP_REST_Response( [ 'success' => false, 'error' => 'El campo title es obligatorio' ], 400 );
    }

    // ── Categorías (por nombre, se crean si no existen) ───────────────────────
    $cat_ids = [];
    foreach ( (array) ( $p['categories'] ?? [] ) as $name ) {
        $name = trim( $name );
        if ( ! $name ) continue;
        $term = get_term_by( 'name', $name, 'category' );
        if ( $term ) {
            $cat_ids[] = $term->term_id;
        } else {
            $new = wp_insert_term( $name, 'category' );
            if ( ! is_wp_error( $new ) ) $cat_ids[] = $new['term_id'];
        }
    }

    // ── Etiquetas (por nombre, se crean si no existen) ────────────────────────
    $tag_ids = [];
    foreach ( (array) ( $p['tags'] ?? [] ) as $name ) {
        $name = trim( $name );
        if ( ! $name ) continue;
        $term = get_term_by( 'name', $name, 'post_tag' );
        if ( $term ) {
            $tag_ids[] = $term->term_id;
        } else {
            $new = wp_insert_term( $name, 'post_tag' );
            if ( ! is_wp_error( $new ) ) $tag_ids[] = $new['term_id'];
        }
    }

    // ── Crear el post ─────────────────────────────────────────────────────────
    $post_id = wp_insert_post( [
        'post_title'    => $title,
        'post_content'  => $content,
        'post_excerpt'  => $excerpt,
        'post_status'   => $status,
        'post_category' => $cat_ids,
        'tags_input'    => $tag_ids,
        'post_date'     => current_time( 'mysql' ),
        'post_date_gmt' => current_time( 'mysql', true ),
    ], true );

    if ( is_wp_error( $post_id ) ) {
        return new WP_REST_Response( [
            'success' => false,
            'error'   => $post_id->get_error_message(),
        ], 500 );
    }

    // ── Imagen destacada ──────────────────────────────────────────────────────
    $feat_url   = trim( $p['featured_image_url']      ?? '' );
    $feat_b64   = trim( $p['featured_image_base64']   ?? '' );
    $feat_fname = sanitize_file_name( $p['featured_image_filename'] ?? 'portada.jpg' );
    $feat_mime  = sanitize_mime_type( $p['featured_image_mimetype'] ?? 'image/jpeg' );

    if ( $feat_url ) {
        autonews_set_featured_from_url( $post_id, $feat_url );
    } elseif ( $feat_b64 ) {
        autonews_set_featured_from_base64( $post_id, $feat_b64, $feat_fname, $feat_mime );
    }

    // ── SEO ───────────────────────────────────────────────────────────────────
    $keyphrase = sanitize_text_field( $p['keyphrase']        ?? '' );
    $meta_desc = sanitize_textarea_field( $p['meta_description'] ?? $excerpt );

    if ( $keyphrase ) {
        // Yoast SEO
        if ( defined( 'WPSEO_VERSION' ) ) {
            update_post_meta( $post_id, '_yoast_wpseo_focuskw',  $keyphrase );
            update_post_meta( $post_id, '_yoast_wpseo_metadesc', $meta_desc );
        }
        // RankMath
        if ( defined( 'RANK_MATH_VERSION' ) ) {
            update_post_meta( $post_id, 'rank_math_focus_keyword', $keyphrase );
            update_post_meta( $post_id, 'rank_math_description',   $meta_desc );
        }
    }

    return new WP_REST_Response( [
        'success'     => true,
        'post_id'     => $post_id,
        'post_url'    => get_permalink( $post_id ),
        'post_status' => $status,
    ] );
}

// ── Helpers de imagen ─────────────────────────────────────────────────────────

function autonews_set_featured_from_url( int $post_id, string $url ): void {
    require_once ABSPATH . 'wp-admin/includes/media.php';
    require_once ABSPATH . 'wp-admin/includes/file.php';
    require_once ABSPATH . 'wp-admin/includes/image.php';

    $media_id = media_sideload_image( $url, $post_id, null, 'id' );
    if ( ! is_wp_error( $media_id ) ) {
        set_post_thumbnail( $post_id, $media_id );
    }
}

function autonews_set_featured_from_base64( int $post_id, string $b64, string $filename, string $mime ): void {
    require_once ABSPATH . 'wp-admin/includes/file.php';
    require_once ABSPATH . 'wp-admin/includes/media.php';
    require_once ABSPATH . 'wp-admin/includes/image.php';

    $data = base64_decode( $b64 );
    if ( ! $data ) return;

    $upload = wp_upload_bits( $filename, null, $data );
    if ( $upload['error'] ) return;

    $att_id = wp_insert_attachment( [
        'post_mime_type' => $mime,
        'post_title'     => sanitize_file_name( $filename ),
        'post_content'   => '',
        'post_status'    => 'inherit',
    ], $upload['file'], $post_id );

    if ( ! is_wp_error( $att_id ) ) {
        wp_update_attachment_metadata( $att_id, wp_generate_attachment_metadata( $att_id, $upload['file'] ) );
        set_post_thumbnail( $post_id, $att_id );
    }
}
